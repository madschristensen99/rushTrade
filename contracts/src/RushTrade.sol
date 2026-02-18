// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @title RushTrade
/// @notice 60-second prediction market: 12 columns × 10 price levels = 120 cells.
/// @dev Each column is 5 seconds wide and settles independently. Users can bet on
///      any cell from round start; a column locks once its 5-second window expires.
///
///      Price-level mapping (top → bottom):
///        Level 1  = price change >= +5%  (most bullish)
///        Level 2  = +4% to +5%
///        ...
///        Level 6  = 0% to +1%
///        ...
///        Level 10 = price change < -4%  (most bearish)
contract RushTrade is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    // ============ STRUCTS ============

    struct Round {
        uint256 roundId;
        uint256 startTime;
        int256  openPrice;  // Set by owner/oracle at round start
    }

    struct Column {
        bool    settled;
        int256  closePrice;
        uint8   winningLevel; // 1-10; 0 if not yet settled
    }

    struct CellPool {
        uint256 totalShares;
        uint256 collateralPool;
        mapping(address => uint256) userShares;
    }

    struct Position {
        address user;
        uint256 roundId;
        uint8   columnId;      // 1-12 (time axis)
        uint8   levelId;       // 1-10 (price axis, 1=top/bullish, 10=bottom/bearish)
        uint256 shares;
        uint256 collateralSpent;
        bool    claimed;
    }

    // ============ CONSTANTS ============

    uint8   public constant NUM_LEVELS      = 10;
    uint8   public constant NUM_COLUMNS     = 12;
    uint256 public constant COLUMN_DURATION = 5;   // seconds per column
    uint256 public constant ROUND_DURATION  = 60;  // seconds per round

    // Level 1 >= +5%; each level spans 100 bps (1%); Level 10 < -4%
    int256 public constant PRICE_RANGE_BPS = 100;  // 1% per level
    int256 public constant MAX_RANGE_BPS   = 500;  // +5% → level 1

    // ============ STATE ============

    IERC20  public immutable collateralToken;
    uint256 public currentRoundId;
    uint256 public feeRateBps = 50;  // 0.5% default
    uint256 public treasuryBalance;

    mapping(uint256 => Round)                                                   public rounds;
    mapping(uint256 => mapping(uint8 => Column))                                public columns;
    mapping(uint256 => mapping(uint8 => mapping(uint8 => CellPool)))            public cellPools;
    mapping(uint256 => Position)                                                public positions;
    mapping(address => uint256[])                                               public userPositions;

    uint256 public nextPositionId = 1;

    // ============ EVENTS ============

    event RoundStarted(uint256 indexed roundId, uint256 startTime);
    event ColumnSettled(uint256 indexed roundId, uint8 indexed columnId, int256 closePrice, uint8 winningLevel);
    event SharesBought(
        uint256 indexed positionId,
        address indexed user,
        uint256 indexed roundId,
        uint8   columnId,
        uint8   levelId,
        uint256 shares,
        uint256 collateralSpent
    );
    event WinningsClaimed(uint256 indexed positionId, address indexed user, uint256 payout);
    event FeeUpdated(uint256 newFeeRateBps);
    event TreasuryWithdrawn(address indexed to, uint256 amount);

    // ============ CONSTRUCTOR ============

    constructor(address _collateralToken) Ownable(msg.sender) {
        require(_collateralToken != address(0), "Invalid collateral token");
        collateralToken = IERC20(_collateralToken);
        _startNewRound();
    }

    // ============ CORE FUNCTIONS ============

    /// @notice Buy shares in a specific cell for the current round.
    /// @param columnId         Time column 1-12 (each 5 s); must not have expired.
    /// @param levelId          Price level 1-10 (1=top/bullish, 10=bottom/bearish).
    /// @param collateralAmount Amount of USDC to spend (6 decimals).
    /// @return positionId      ID of the newly created position.
    function buyShares(
        uint8   columnId,
        uint8   levelId,
        uint256 collateralAmount
    ) external nonReentrant returns (uint256 positionId) {
        require(columnId >= 1 && columnId <= NUM_COLUMNS, "Invalid column");
        require(levelId  >= 1 && levelId  <= NUM_LEVELS,  "Invalid level");
        require(collateralAmount > 0, "Amount must be > 0");

        Round storage round = rounds[currentRoundId];
        require(round.openPrice != 0, "Open price not set");

        uint256 columnEndTime = round.startTime + uint256(columnId) * COLUMN_DURATION;
        require(block.timestamp < columnEndTime, "Column already expired");
        require(!columns[currentRoundId][columnId].settled, "Column already settled");

        collateralToken.safeTransferFrom(msg.sender, address(this), collateralAmount);

        CellPool storage pool = cellPools[currentRoundId][columnId][levelId];
        uint256 shares = _calculateSharesForCollateral(pool, collateralAmount);
        require(shares > 0, "Insufficient collateral");

        pool.totalShares          += shares;
        pool.collateralPool       += collateralAmount;
        pool.userShares[msg.sender] += shares;

        positionId = nextPositionId++;
        positions[positionId] = Position({
            user:            msg.sender,
            roundId:         currentRoundId,
            columnId:        columnId,
            levelId:         levelId,
            shares:          shares,
            collateralSpent: collateralAmount,
            claimed:         false
        });

        userPositions[msg.sender].push(positionId);

        emit SharesBought(positionId, msg.sender, currentRoundId, columnId, levelId, shares, collateralAmount);
    }

    /// @notice Settle a column once its 5-second window has elapsed.
    /// @param columnId   Column to settle (1-12).
    /// @param closePrice BTC/USD price (with same precision as openPrice) at column end.
    function settleColumn(uint8 columnId, int256 closePrice) external onlyOwner {
        require(columnId >= 1 && columnId <= NUM_COLUMNS, "Invalid column");

        Round storage round = rounds[currentRoundId];
        require(round.openPrice != 0, "Open price not set");

        uint256 columnEndTime = round.startTime + uint256(columnId) * COLUMN_DURATION;
        require(block.timestamp >= columnEndTime, "Column not ended yet");
        require(!columns[currentRoundId][columnId].settled, "Already settled");

        uint8 winningLevel = _calculateWinningLevel(round.openPrice, closePrice);

        columns[currentRoundId][columnId] = Column({
            settled:      true,
            closePrice:   closePrice,
            winningLevel: winningLevel
        });

        emit ColumnSettled(currentRoundId, columnId, closePrice, winningLevel);

        // Roll over to a new round after the last column
        if (columnId == NUM_COLUMNS) {
            _startNewRound();
        }
    }

    /// @notice Claim winnings for a settled, winning position.
    /// @param positionId ID of the position to claim.
    function claimWinnings(uint256 positionId) external nonReentrant {
        Position storage position = positions[positionId];
        require(position.user == msg.sender, "Not your position");
        require(!position.claimed,           "Already claimed");
        require(position.shares > 0,         "No shares");

        Column storage col = columns[position.roundId][position.columnId];
        require(col.settled,                             "Column not settled");
        require(position.levelId == col.winningLevel,    "Not winning level");

        CellPool storage pool = cellPools[position.roundId][position.columnId][position.levelId];

        // Prize pool = sum of collateral from all losing levels in this column
        uint256 totalPrizePool = 0;
        for (uint8 i = 1; i <= NUM_LEVELS; i++) {
            if (i != col.winningLevel) {
                totalPrizePool += cellPools[position.roundId][position.columnId][i].collateralPool;
            }
        }

        uint256 prizeShare = pool.totalShares > 0
            ? (totalPrizePool * position.shares) / pool.totalShares
            : 0;

        uint256 fee         = (prizeShare * feeRateBps) / 10000;
        treasuryBalance    += fee;
        uint256 totalPayout = position.collateralSpent + prizeShare - fee;

        position.claimed = true;
        collateralToken.safeTransfer(msg.sender, totalPayout);

        emit WinningsClaimed(positionId, msg.sender, totalPayout);
    }

    /// @notice Batch-claim winnings across multiple positions.
    /// @param positionIds Array of position IDs to claim.
    function claimMultipleWinnings(uint256[] calldata positionIds) external nonReentrant {
        uint256 totalPayout = 0;

        for (uint256 i = 0; i < positionIds.length; i++) {
            Position storage position = positions[positionIds[i]];
            require(position.user == msg.sender, "Not your position");
            require(!position.claimed,           "Already claimed");

            Column storage col = columns[position.roundId][position.columnId];
            if (!col.settled || position.levelId != col.winningLevel || position.shares == 0) continue;

            CellPool storage pool = cellPools[position.roundId][position.columnId][position.levelId];

            uint256 totalPrizePool = 0;
            for (uint8 j = 1; j <= NUM_LEVELS; j++) {
                if (j != col.winningLevel) {
                    totalPrizePool += cellPools[position.roundId][position.columnId][j].collateralPool;
                }
            }

            uint256 prizeShare = pool.totalShares > 0
                ? (totalPrizePool * position.shares) / pool.totalShares
                : 0;

            uint256 fee    = (prizeShare * feeRateBps) / 10000;
            treasuryBalance += fee;
            uint256 payout  = position.collateralSpent + prizeShare - fee;

            position.claimed = true;
            totalPayout     += payout;

            emit WinningsClaimed(positionIds[i], msg.sender, payout);
        }

        require(totalPayout > 0, "No winnings to claim");
        collateralToken.safeTransfer(msg.sender, totalPayout);
    }

    // ============ VIEW FUNCTIONS ============

    function getCurrentRound() external view returns (Round memory) {
        return rounds[currentRoundId];
    }

    function getRound(uint256 roundId) external view returns (Round memory) {
        return rounds[roundId];
    }

    function getColumn(uint256 roundId, uint8 columnId) external view returns (Column memory) {
        return columns[roundId][columnId];
    }

    function getPosition(uint256 positionId) external view returns (Position memory) {
        return positions[positionId];
    }

    function getUserPositions(address user) external view returns (uint256[] memory) {
        return userPositions[user];
    }

    function getCellPool(uint256 roundId, uint8 columnId, uint8 levelId)
        external view returns (uint256 totalShares, uint256 collateralPool)
    {
        CellPool storage pool = cellPools[roundId][columnId][levelId];
        return (pool.totalShares, pool.collateralPool);
    }

    /// @notice Map a price change (in bps) to a winning level.
    ///         Level 1 = >= +5% (top/bullish), Level 10 = < -4% (bottom/bearish).
    function getLevelForPriceChange(int256 priceChangeBps) public pure returns (uint8) {
        if (priceChangeBps >= MAX_RANGE_BPS) return 1;
        int256 bottomBound = MAX_RANGE_BPS - int256(uint256(NUM_LEVELS)) * PRICE_RANGE_BPS;
        if (priceChangeBps < bottomBound) return NUM_LEVELS;
        int256 idx = (MAX_RANGE_BPS - priceChangeBps) / PRICE_RANGE_BPS;
        return uint8(uint256(idx) + 1);
    }

    // ============ ADMIN FUNCTIONS ============

    /// @notice Set the opening price for the current round (called by oracle/owner).
    function setOpenPrice(int256 openPrice) external onlyOwner {
        Round storage round = rounds[currentRoundId];
        require(round.openPrice == 0, "Open price already set");
        require(openPrice > 0, "Invalid price");
        round.openPrice = openPrice;
    }

    function setFeeRate(uint256 newFeeRateBps) external onlyOwner {
        require(newFeeRateBps <= 1000, "Fee too high");
        feeRateBps = newFeeRateBps;
        emit FeeUpdated(newFeeRateBps);
    }

    function withdrawTreasury(address to) external onlyOwner {
        require(to != address(0), "Invalid address");
        uint256 amount = treasuryBalance;
        require(amount > 0, "No treasury balance");
        treasuryBalance = 0;
        collateralToken.safeTransfer(to, amount);
        emit TreasuryWithdrawn(to, amount);
    }

    function emergencyWithdraw(address token, address to, uint256 amount) external onlyOwner {
        require(to != address(0), "Invalid address");
        IERC20(token).safeTransfer(to, amount);
    }

    // ============ INTERNAL ============

    function _startNewRound() internal {
        currentRoundId++;
        rounds[currentRoundId] = Round({
            roundId:   currentRoundId,
            startTime: block.timestamp,
            openPrice: 0
        });
        emit RoundStarted(currentRoundId, block.timestamp);
    }

    /// @dev First buyer gets 1:1; subsequent buyers use constant-product bonding curve.
    function _calculateSharesForCollateral(
        CellPool storage pool,
        uint256 collateralAmount
    ) internal view returns (uint256) {
        if (pool.totalShares == 0) return collateralAmount;
        return (collateralAmount * pool.totalShares) / pool.collateralPool;
    }

    function _calculateWinningLevel(int256 openPrice, int256 closePrice) internal pure returns (uint8) {
        require(openPrice > 0, "Invalid open price");
        int256 priceChangeBps = ((closePrice - openPrice) * 10000) / openPrice;
        return getLevelForPriceChange(priceChangeBps);
    }
}
