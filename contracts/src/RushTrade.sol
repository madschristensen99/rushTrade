// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @title RushTrade
/// @notice Multi-token pool prediction market for 60-second BTC price movements
/// @dev Each box (1-10) represents a different outcome token with its own liquidity pool
contract RushTrade is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ============ STRUCTS ============

    struct Round {
        uint256 roundId;
        uint256 startTime;
        uint256 endTime;
        int256 openPrice;
        int256 closePrice;
        uint8 winningBox; // 0 = not settled, 1-10 = winning box
        bool settled;
    }

    struct BoxPool {
        uint256 totalShares; // Total outcome tokens minted for this box
        uint256 collateralPool; // Total collateral in this box's pool
        mapping(address => uint256) userShares; // User's shares in this box
    }

    struct Position {
        address user;
        uint256 roundId;
        uint8 boxId; // 1-10
        uint256 shares; // Amount of outcome tokens owned
        uint256 collateralSpent;
        bool claimed;
    }

    // ============ STATE VARIABLES ============

    IERC20 public immutable collateralToken; // USDC
    
    uint256 public currentRoundId;
    uint256 public constant ROUND_DURATION = 60 seconds;
    uint256 public constant NUM_BOXES = 10; // Boxes 1-10
    
    // Fee configuration (in basis points, 100 = 1%)
    uint256 public feeRateBps = 50; // 0.5% default
    uint256 public treasuryBalance;
    
    // Price range per box (in basis points, e.g., 100 = 1%)
    // Box 1 = -5% to -4%, Box 2 = -4% to -3%, ..., Box 6 = 0% to +1%, ..., Box 10 = +4% to +5%
    int256 public constant PRICE_RANGE_BPS = 100; // 1% per box
    int256 public constant MIN_RANGE_BPS = -500; // -5% (Box 1 starts here)
    
    // Mappings
    mapping(uint256 => Round) public rounds;
    mapping(uint256 => mapping(uint8 => BoxPool)) public roundBoxPools; // roundId => boxId => BoxPool
    mapping(uint256 => Position) public positions; // positionId => Position
    mapping(address => uint256[]) public userPositions; // user => positionIds
    
    uint256 public nextPositionId = 1;

    // ============ EVENTS ============

    event RoundStarted(uint256 indexed roundId, uint256 startTime, int256 openPrice);
    event RoundEnded(uint256 indexed roundId, int256 closePrice, uint8 winningBox);
    event SharesBought(
        uint256 indexed positionId,
        address indexed user,
        uint256 indexed roundId,
        uint8 boxId,
        uint256 shares,
        uint256 collateralSpent
    );
    event SharesSold(
        uint256 indexed positionId,
        address indexed user,
        uint256 indexed roundId,
        uint8 boxId,
        uint256 shares,
        uint256 collateralReceived
    );
    event WinningsClaimed(uint256 indexed positionId, address indexed user, uint256 payout);
    event FeeUpdated(uint256 newFeeRateBps);
    event TreasuryWithdrawn(address indexed to, uint256 amount);

    // ============ CONSTRUCTOR ============

    constructor(address _collateralToken) Ownable(msg.sender) {
        require(_collateralToken != address(0), "Invalid collateral token");
        collateralToken = IERC20(_collateralToken);
        
        // Start first round
        _startNewRound();
    }

    // ============ CORE FUNCTIONS ============

    /// @notice Buy shares in a specific box for the current round
    /// @param boxId The box to buy shares in (1-10)
    /// @param collateralAmount Amount of collateral to spend
    /// @return positionId The ID of the created position
    function buyShares(
        uint8 boxId,
        uint256 collateralAmount
    ) external nonReentrant returns (uint256 positionId) {
        require(boxId >= 1 && boxId <= NUM_BOXES, "Invalid box ID");
        require(collateralAmount > 0, "Amount must be > 0");
        
        Round storage round = rounds[currentRoundId];
        require(!round.settled, "Round already settled");
        require(block.timestamp < round.endTime, "Round ended");
        require(round.openPrice != 0, "Open price not set");
        
        // Transfer collateral from user
        collateralToken.safeTransferFrom(msg.sender, address(this), collateralAmount);
        
        // Calculate shares using bonding curve (constant product AMM)
        BoxPool storage pool = roundBoxPools[currentRoundId][boxId];
        uint256 shares = _calculateSharesForCollateral(pool, collateralAmount);
        require(shares > 0, "Insufficient collateral");
        
        // Update pool
        pool.totalShares += shares;
        pool.collateralPool += collateralAmount;
        pool.userShares[msg.sender] += shares;
        
        // Create position
        positionId = nextPositionId++;
        positions[positionId] = Position({
            user: msg.sender,
            roundId: currentRoundId,
            boxId: boxId,
            shares: shares,
            collateralSpent: collateralAmount,
            claimed: false
        });
        
        userPositions[msg.sender].push(positionId);
        
        emit SharesBought(positionId, msg.sender, currentRoundId, boxId, shares, collateralAmount);
    }

    /// @notice Sell shares back to the pool before round ends
    /// @param positionId The position ID to sell
    /// @param sharesToSell Amount of shares to sell
    function sellShares(
        uint256 positionId,
        uint256 sharesToSell
    ) external nonReentrant {
        Position storage position = positions[positionId];
        require(position.user == msg.sender, "Not your position");
        require(sharesToSell > 0 && sharesToSell <= position.shares, "Invalid amount");
        
        Round storage round = rounds[position.roundId];
        require(!round.settled, "Round already settled");
        require(block.timestamp < round.endTime, "Round ended");
        
        // Calculate collateral to return using bonding curve
        BoxPool storage pool = roundBoxPools[position.roundId][position.boxId];
        uint256 collateralToReturn = _calculateCollateralForShares(pool, sharesToSell);
        
        // Update pool
        pool.totalShares -= sharesToSell;
        pool.collateralPool -= collateralToReturn;
        pool.userShares[msg.sender] -= sharesToSell;
        
        // Update position
        position.shares -= sharesToSell;
        
        // Transfer collateral back to user (minus fee)
        uint256 fee = (collateralToReturn * feeRateBps) / 10000;
        treasuryBalance += fee;
        uint256 netCollateral = collateralToReturn - fee;
        
        collateralToken.safeTransfer(msg.sender, netCollateral);
        
        emit SharesSold(positionId, msg.sender, position.roundId, position.boxId, sharesToSell, netCollateral);
    }

    /// @notice End current round and start a new one
    /// @param closePrice The closing price for the round
    function settleRound(int256 closePrice) external onlyOwner {
        Round storage round = rounds[currentRoundId];
        require(!round.settled, "Round already settled");
        require(block.timestamp >= round.endTime, "Round not ended yet");
        require(round.openPrice != 0, "Open price not set");
        
        round.closePrice = closePrice;
        round.settled = true;
        
        // Calculate winning box based on price change
        uint8 winningBox = _calculateWinningBox(round.openPrice, closePrice);
        round.winningBox = winningBox;
        
        emit RoundEnded(currentRoundId, closePrice, winningBox);
        
        // Start new round
        _startNewRound();
    }

    /// @notice Claim winnings for a position
    /// @param positionId The position ID to claim
    function claimWinnings(uint256 positionId) external nonReentrant {
        Position storage position = positions[positionId];
        require(position.user == msg.sender, "Not your position");
        require(!position.claimed, "Already claimed");
        require(position.shares > 0, "No shares");
        
        Round storage round = rounds[position.roundId];
        require(round.settled, "Round not settled");
        require(position.boxId == round.winningBox, "Not winning box");
        
        // Calculate payout: user gets their share of the total pool
        BoxPool storage pool = roundBoxPools[position.roundId][position.boxId];
        
        // Collect all collateral from losing boxes
        uint256 totalPrizePool = 0;
        for (uint8 i = 1; i <= NUM_BOXES; i++) {
            if (i != round.winningBox) {
                totalPrizePool += roundBoxPools[position.roundId][i].collateralPool;
            }
        }
        
        // User's share of the prize pool
        uint256 prizeShare = (totalPrizePool * position.shares) / pool.totalShares;
        
        // Total payout = original collateral + prize share
        uint256 totalPayout = position.collateralSpent + prizeShare;
        
        // Deduct fee from winnings only
        uint256 fee = (prizeShare * feeRateBps) / 10000;
        treasuryBalance += fee;
        totalPayout -= fee;
        
        position.claimed = true;
        
        // Transfer payout
        collateralToken.safeTransfer(msg.sender, totalPayout);
        
        emit WinningsClaimed(positionId, msg.sender, totalPayout);
    }

    /// @notice Batch claim multiple positions
    /// @param positionIds Array of position IDs to claim
    function claimMultipleWinnings(uint256[] calldata positionIds) external nonReentrant {
        uint256 totalPayout = 0;
        
        for (uint256 i = 0; i < positionIds.length; i++) {
            uint256 positionId = positionIds[i];
            Position storage position = positions[positionId];
            
            require(position.user == msg.sender, "Not your position");
            require(!position.claimed, "Already claimed");
            
            Round storage round = rounds[position.roundId];
            require(round.settled, "Round not settled");
            
            if (position.boxId == round.winningBox && position.shares > 0) {
                BoxPool storage pool = roundBoxPools[position.roundId][position.boxId];
                
                uint256 totalPrizePool = 0;
                for (uint8 j = 1; j <= NUM_BOXES; j++) {
                    if (j != round.winningBox) {
                        totalPrizePool += roundBoxPools[position.roundId][j].collateralPool;
                    }
                }
                
                uint256 prizeShare = (totalPrizePool * position.shares) / pool.totalShares;
                uint256 payout = position.collateralSpent + prizeShare;
                
                uint256 fee = (prizeShare * feeRateBps) / 10000;
                treasuryBalance += fee;
                payout -= fee;
                
                position.claimed = true;
                totalPayout += payout;
                
                emit WinningsClaimed(positionId, msg.sender, payout);
            }
        }
        
        require(totalPayout > 0, "No winnings to claim");
        collateralToken.safeTransfer(msg.sender, totalPayout);
    }

    // ============ VIEW FUNCTIONS ============

    /// @notice Get current round info
    function getCurrentRound() external view returns (Round memory) {
        return rounds[currentRoundId];
    }

    /// @notice Get round by ID
    function getRound(uint256 roundId) external view returns (Round memory) {
        return rounds[roundId];
    }

    /// @notice Get position by ID
    function getPosition(uint256 positionId) external view returns (Position memory) {
        return positions[positionId];
    }

    /// @notice Get all positions for a user
    function getUserPositions(address user) external view returns (uint256[] memory) {
        return userPositions[user];
    }

    /// @notice Get box pool info
    function getBoxPool(uint256 roundId, uint8 boxId) external view returns (
        uint256 totalShares,
        uint256 collateralPool
    ) {
        BoxPool storage pool = roundBoxPools[roundId][boxId];
        return (pool.totalShares, pool.collateralPool);
    }

    /// @notice Get user's shares in a specific box
    function getUserShares(uint256 roundId, uint8 boxId, address user) external view returns (uint256) {
        return roundBoxPools[roundId][boxId].userShares[user];
    }

    /// @notice Calculate potential payout for a position if it wins
    function calculatePotentialPayout(uint256 positionId) external view returns (uint256) {
        Position storage position = positions[positionId];
        Round storage round = rounds[position.roundId];
        
        if (!round.settled) {
            return 0;
        }
        
        if (position.boxId != round.winningBox || position.shares == 0) {
            return 0;
        }
        
        BoxPool storage pool = roundBoxPools[position.roundId][position.boxId];
        
        uint256 totalPrizePool = 0;
        for (uint8 i = 1; i <= NUM_BOXES; i++) {
            if (i != round.winningBox) {
                totalPrizePool += roundBoxPools[position.roundId][i].collateralPool;
            }
        }
        
        uint256 prizeShare = (totalPrizePool * position.shares) / pool.totalShares;
        uint256 totalPayout = position.collateralSpent + prizeShare;
        uint256 fee = (prizeShare * feeRateBps) / 10000;
        
        return totalPayout - fee;
    }

    /// @notice Get the box ID for a given price change percentage
    /// @param priceChangeBps Price change in basis points (e.g., 150 = +1.5%)
    function getBoxForPriceChange(int256 priceChangeBps) public pure returns (uint8) {
        // Box 1: -500 to -400 bps (-5% to -4%)
        // Box 2: -400 to -300 bps (-4% to -3%)
        // ...
        // Box 5: -100 to 0 bps (-1% to 0%)
        // Box 6: 0 to +100 bps (0% to +1%)
        // ...
        // Box 10: +400 to +500 bps (+4% to +5%)
        
        if (priceChangeBps < MIN_RANGE_BPS) return 1;
        if (priceChangeBps >= MIN_RANGE_BPS + (int256(NUM_BOXES) * PRICE_RANGE_BPS)) return uint8(NUM_BOXES);
        
        int256 boxIndex = (priceChangeBps - MIN_RANGE_BPS) / PRICE_RANGE_BPS;
        return uint8(uint256(boxIndex) + 1);
    }

    // ============ ADMIN FUNCTIONS ============

    /// @notice Update fee rate
    /// @param newFeeRateBps New fee rate in basis points
    function setFeeRate(uint256 newFeeRateBps) external onlyOwner {
        require(newFeeRateBps <= 1000, "Fee too high"); // Max 10%
        feeRateBps = newFeeRateBps;
        emit FeeUpdated(newFeeRateBps);
    }

    /// @notice Withdraw treasury fees
    /// @param to Address to send fees to
    function withdrawTreasury(address to) external onlyOwner {
        require(to != address(0), "Invalid address");
        uint256 amount = treasuryBalance;
        require(amount > 0, "No treasury balance");
        
        treasuryBalance = 0;
        collateralToken.safeTransfer(to, amount);
        
        emit TreasuryWithdrawn(to, amount);
    }

    /// @notice Emergency withdraw (only if something goes wrong)
    /// @param token Token to withdraw
    /// @param to Address to send to
    /// @param amount Amount to withdraw
    function emergencyWithdraw(
        address token,
        address to,
        uint256 amount
    ) external onlyOwner {
        require(to != address(0), "Invalid address");
        IERC20(token).safeTransfer(to, amount);
    }

    // ============ INTERNAL FUNCTIONS ============

    /// @notice Start a new round
    function _startNewRound() internal {
        currentRoundId++;
        
        rounds[currentRoundId] = Round({
            roundId: currentRoundId,
            startTime: block.timestamp,
            endTime: block.timestamp + ROUND_DURATION,
            openPrice: 0, // Will be set by oracle/owner
            closePrice: 0,
            winningBox: 0,
            settled: false
        });
        
        emit RoundStarted(currentRoundId, block.timestamp, 0);
    }

    /// @notice Calculate shares to mint for given collateral (constant product AMM)
    /// @dev Uses formula: shares = collateral * (1 + totalShares) / (1 + collateralPool)
    function _calculateSharesForCollateral(
        BoxPool storage pool,
        uint256 collateralAmount
    ) internal view returns (uint256) {
        if (pool.totalShares == 0) {
            // First buyer gets 1:1 ratio
            return collateralAmount;
        }
        
        // Bonding curve: more collateral in pool = more expensive shares
        // shares = (collateralAmount * totalShares) / collateralPool
        return (collateralAmount * pool.totalShares) / pool.collateralPool;
    }

    /// @notice Calculate collateral to return for given shares
    function _calculateCollateralForShares(
        BoxPool storage pool,
        uint256 shares
    ) internal view returns (uint256) {
        require(pool.totalShares > 0, "No shares in pool");
        
        // collateral = (shares * collateralPool) / totalShares
        return (shares * pool.collateralPool) / pool.totalShares;
    }

    /// @notice Calculate which box won based on price change
    function _calculateWinningBox(int256 openPrice, int256 closePrice) internal pure returns (uint8) {
        require(openPrice > 0, "Invalid open price");
        
        // Calculate price change in basis points
        int256 priceChange = closePrice - openPrice;
        int256 priceChangeBps = (priceChange * 10000) / openPrice;
        
        return getBoxForPriceChange(priceChangeBps);
    }

    /// @notice Set opening price for current round (called by oracle/owner)
    /// @param openPrice The opening price
    function setOpenPrice(int256 openPrice) external onlyOwner {
        Round storage round = rounds[currentRoundId];
        require(round.openPrice == 0, "Open price already set");
        require(openPrice > 0, "Invalid price");
        round.openPrice = openPrice;
    }
}
