// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/access/Ownable2Step.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "./interfaces/IConditionalTokens.sol";

/// @title MarketFactory
/// @notice Creates and tracks binary prediction markets backed by the Gnosis CTF.
///
/// Flow
/// ----
/// 1. Owner approves a collateral token (e.g. USDC on Monad).
/// 2. Owner calls createMarket(), which prepares a condition on the CTF with
///    outcomeSlotCount = 2.  Outcome 0 = YES (indexSet 0b01), outcome 1 = NO (indexSet 0b10).
/// 3. Anyone can split collateral into YES/NO positions via the CTF directly.
/// 4. The designated oracle calls reportPayouts() on the CTF to resolve the market.
///    A resolved-YES market reports [1, 0]; resolved-NO reports [0, 1].
/// 5. Winners call redeemPositions() on the CTF to claim their collateral.
///
/// Resolution flow (oracle → CTF, not this contract)
/// --------------------------------------------------
/// The oracle address set per market calls `ctf.reportPayouts(questionId, payouts)`.
/// MarketFactory emits a MarketResolved event by listening for this off-chain,
/// or you can integrate an on-chain oracle adapter that calls resolveMarket() here.
contract MarketFactory is Ownable2Step {
    // -------------------------------------------------------------------------
    // Types
    // -------------------------------------------------------------------------

    struct Market {
        bytes32 questionId;
        bytes32 conditionId;
        address oracle;
        address collateralToken;
        uint256 resolutionTime; // earliest timestamp at which the oracle may resolve
        bool resolved;
        string title;
        string description;
        string category;        // e.g. "Sports", "Crypto", "Politics"
    }

    // -------------------------------------------------------------------------
    // State
    // -------------------------------------------------------------------------

    IConditionalTokens public immutable ctf;

    /// @notice Collateral tokens whitelisted for use in markets.
    mapping(address => bool) public approvedCollateral;

    /// @notice conditionId => Market metadata.
    mapping(bytes32 => Market) public markets;

    /// @notice All condition IDs in creation order (for pagination).
    bytes32[] public allMarkets;

    // -------------------------------------------------------------------------
    // Events
    // -------------------------------------------------------------------------

    event MarketCreated(
        bytes32 indexed conditionId,
        bytes32 indexed questionId,
        address indexed oracle,
        address collateralToken,
        uint256 resolutionTime,
        string title,
        string category
    );

    event MarketResolved(bytes32 indexed conditionId, uint256[] payouts);

    event CollateralApprovalSet(address indexed token, bool approved);

    // -------------------------------------------------------------------------
    // Constructor
    // -------------------------------------------------------------------------

    constructor(address _ctf, address _initialOwner) Ownable(_initialOwner) {
        require(_ctf != address(0), "MarketFactory: zero CTF address");
        ctf = IConditionalTokens(_ctf);
    }

    // -------------------------------------------------------------------------
    // Owner admin
    // -------------------------------------------------------------------------

    /// @notice Whitelist or blacklist a collateral token.
    function setCollateralApproval(address token, bool approved) external onlyOwner {
        require(token != address(0), "MarketFactory: zero token address");
        approvedCollateral[token] = approved;
        emit CollateralApprovalSet(token, approved);
    }

    // -------------------------------------------------------------------------
    // Market creation
    // -------------------------------------------------------------------------

    /// @notice Create a new binary (YES/NO) prediction market.
    /// @param questionId      Unique bytes32 identifier.  Convention: keccak256(abi.encode(title, salt)).
    /// @param oracle          Address authorised to resolve this market via ctf.reportPayouts().
    /// @param collateralToken ERC-20 collateral (must be whitelisted).
    /// @param resolutionTime  Unix timestamp: the oracle should not resolve before this time.
    /// @param title           Human-readable market title shown in the UI.
    /// @param description     Extended description / resolution criteria.
    /// @param category        Market category tag.
    /// @return conditionId    The CTF condition ID derived from (oracle, questionId, 2).
    function createMarket(
        bytes32 questionId,
        address oracle,
        address collateralToken,
        uint256 resolutionTime,
        string calldata title,
        string calldata description,
        string calldata category
    ) external onlyOwner returns (bytes32 conditionId) {
        require(approvedCollateral[collateralToken], "MarketFactory: collateral not approved");
        require(oracle != address(0), "MarketFactory: zero oracle address");
        require(resolutionTime > block.timestamp, "MarketFactory: resolution time in the past");
        require(bytes(title).length > 0, "MarketFactory: empty title");

        // Binary market: 2 outcome slots.
        // The factory itself is the CTF oracle so that resolveMarket() can call
        // ctf.reportPayouts() — the CTF uses msg.sender as the oracle identity,
        // so the address used in prepareCondition must match whoever calls reportPayouts.
        conditionId = ctf.getConditionId(address(this), questionId, 2);
        require(markets[conditionId].oracle == address(0), "MarketFactory: market already exists");

        ctf.prepareCondition(address(this), questionId, 2);

        markets[conditionId] = Market({
            questionId: questionId,
            conditionId: conditionId,
            oracle: oracle,
            collateralToken: collateralToken,
            resolutionTime: resolutionTime,
            resolved: false,
            title: title,
            description: description,
            category: category
        });

        allMarkets.push(conditionId);

        emit MarketCreated(conditionId, questionId, oracle, collateralToken, resolutionTime, title, category);
    }

    // -------------------------------------------------------------------------
    // Resolution (oracle-triggered via this contract)
    // -------------------------------------------------------------------------

    /// @notice Called by the market's oracle to resolve a market.
    ///         Payouts: YES win = [1, 0], NO win = [0, 1], invalid = [1, 1].
    /// @dev    The actual reportPayouts call is forwarded to the CTF.
    ///         Only the designated oracle for this market may call this.
    function resolveMarket(bytes32 conditionId, uint256[] calldata payouts) external {
        Market storage market = markets[conditionId];
        require(market.oracle != address(0), "MarketFactory: market does not exist");
        require(msg.sender == market.oracle, "MarketFactory: caller is not the oracle");
        require(!market.resolved, "MarketFactory: already resolved");
        require(block.timestamp >= market.resolutionTime, "MarketFactory: too early to resolve");
        require(payouts.length == 2, "MarketFactory: must provide 2 payouts for binary market");

        market.resolved = true;

        // Forward resolution to the CTF.
        ctf.reportPayouts(market.questionId, payouts);

        emit MarketResolved(conditionId, payouts);
    }

    // -------------------------------------------------------------------------
    // Views
    // -------------------------------------------------------------------------

    function getMarket(bytes32 conditionId) external view returns (Market memory) {
        return markets[conditionId];
    }

    function totalMarkets() external view returns (uint256) {
        return allMarkets.length;
    }

    /// @notice Paginated list of market condition IDs (newest first).
    function getMarkets(uint256 offset, uint256 limit)
        external
        view
        returns (bytes32[] memory result)
    {
        uint256 total = allMarkets.length;
        if (offset >= total) return new bytes32[](0);

        uint256 end = offset + limit;
        if (end > total) end = total;
        uint256 len = end - offset;

        result = new bytes32[](len);
        for (uint256 i = 0; i < len; i++) {
            // Reverse order so newest markets come first.
            result[i] = allMarkets[total - 1 - offset - i];
        }
    }

    /// @notice Convenience: derive the YES/NO ERC-1155 position IDs for a market.
    ///         Uses the canonical Gnosis CTHelpers formulas via the CTF interface.
    ///           YES = indexSet 0b01 = 1
    ///           NO  = indexSet 0b10 = 2
    function getPositionIds(bytes32 conditionId)
        external
        view
        returns (uint256 yesId, uint256 noId)
    {
        Market storage m = markets[conditionId];
        require(m.oracle != address(0), "MarketFactory: market does not exist");
        IERC20 collateral = IERC20(m.collateralToken);
        bytes32 yesCollection = ctf.getCollectionId(bytes32(0), conditionId, 1);
        bytes32 noCollection  = ctf.getCollectionId(bytes32(0), conditionId, 2);
        yesId = ctf.getPositionId(collateral, yesCollection);
        noId  = ctf.getPositionId(collateral, noCollection);
    }
}
