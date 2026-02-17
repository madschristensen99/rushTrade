// SPDX-License-Identifier: LGPL-3.0
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC20/IERC20.sol";

/// @title IConditionalTokens
/// @notice Interface matching the ConditionalTokens.sol implementation in this repo.
///
/// Storage layout
/// --------------
///   mapping(bytes32 => address)   oracles             – oracle per condition
///   mapping(bytes32 => uint256)   outcomeSlotCounts   – slot count per condition
///   mapping(bytes32 => uint256[]) payoutNumerators    – indexed getter: (conditionId, index)
///   mapping(bytes32 => uint256)   payoutDenominator   – non-zero iff resolved
///
/// Detecting resolution
/// --------------------
///   payoutDenominator[conditionId] > 0  <=>  condition is resolved
///   OR call isResolved(conditionId)
///
/// Computing position IDs for a binary market (no parent collection)
/// -----------------------------------------------------------------
///   bytes32 yesCollection = getCollectionId(bytes32(0), conditionId, 1);
///   bytes32 noCollection  = getCollectionId(bytes32(0), conditionId, 2);
///   uint256 yesId = getPositionId(collateral, yesCollection);
///   uint256 noId  = getPositionId(collateral, noCollection);
///   OR: (yesId, noId) = getPositionIds(collateral, conditionId);
interface IConditionalTokens {
    // -------------------------------------------------------------------------
    // Events
    // -------------------------------------------------------------------------

    event ConditionPreparation(
        bytes32 indexed conditionId,
        address indexed oracle,
        bytes32 indexed questionId,
        uint256 outcomeSlotCount
    );

    event ConditionResolution(
        bytes32 indexed conditionId,
        address indexed oracle,
        bytes32 indexed questionId,
        uint256 outcomeSlotCount,
        uint256[] payoutNumerators
    );

    event PositionSplit(
        address indexed stakeholder,
        IERC20 collateralToken,
        bytes32 indexed parentCollectionId,
        bytes32 indexed conditionId,
        uint256[] partition,
        uint256 amount
    );

    event PositionsMerge(
        address indexed stakeholder,
        IERC20 collateralToken,
        bytes32 indexed parentCollectionId,
        bytes32 indexed conditionId,
        uint256[] partition,
        uint256 amount
    );

    event PayoutRedemption(
        address indexed redeemer,
        IERC20 indexed collateralToken,
        bytes32 indexed parentCollectionId,
        bytes32 conditionId,
        uint256[] indexSets,
        uint256 payout
    );

    // -------------------------------------------------------------------------
    // Storage public getters
    // -------------------------------------------------------------------------

    /// @notice Oracle address registered for a condition.
    function oracles(bytes32 conditionId) external view returns (address);

    /// @notice Number of outcome slots for a condition (0 if not yet prepared).
    function outcomeSlotCounts(bytes32 conditionId) external view returns (uint256);

    /// @notice Payout numerator for a single outcome slot.
    function payoutNumerators(bytes32 conditionId, uint256 index) external view returns (uint256);

    /// @notice Sum of payout numerators. Non-zero iff the condition has been resolved.
    function payoutDenominator(bytes32 conditionId) external view returns (uint256);

    // -------------------------------------------------------------------------
    // Core functions
    // -------------------------------------------------------------------------

    function prepareCondition(address oracle, bytes32 questionId, uint256 outcomeSlotCount) external;

    function reportPayouts(bytes32 questionId, uint256[] calldata payouts) external;

    function splitPosition(
        IERC20 collateralToken,
        bytes32 parentCollectionId,
        bytes32 conditionId,
        uint256[] calldata partition,
        uint256 amount
    ) external;

    function mergePositions(
        IERC20 collateralToken,
        bytes32 parentCollectionId,
        bytes32 conditionId,
        uint256[] calldata partition,
        uint256 amount
    ) external;

    function redeemPositions(
        IERC20 collateralToken,
        bytes32 parentCollectionId,
        bytes32 conditionId,
        uint256[] calldata indexSets
    ) external;

    // -------------------------------------------------------------------------
    // Pure / view helpers
    // -------------------------------------------------------------------------

    /// @notice conditionId = keccak256(abi.encodePacked(oracle, questionId, outcomeSlotCount))
    function getConditionId(address oracle, bytes32 questionId, uint256 outcomeSlotCount)
        external
        pure
        returns (bytes32);

    /// @notice collectionId = parentCollectionId XOR keccak256(abi.encodePacked(conditionId, indexSet))
    function getCollectionId(bytes32 parentCollectionId, bytes32 conditionId, uint256 indexSet)
        external
        pure
        returns (bytes32);

    /// @notice positionId = uint(keccak256(abi.encodePacked(collateralToken, collectionId)))
    function getPositionId(IERC20 collateralToken, bytes32 collectionId)
        external
        pure
        returns (uint256);

    /// @notice Convenience: YES and NO position IDs for a top-level binary market.
    ///         YES = indexSet 0b01 = 1, NO = indexSet 0b10 = 2.
    function getPositionIds(IERC20 collateral, bytes32 conditionId)
        external
        pure
        returns (uint256 yesPositionId, uint256 noPositionId);

    /// @notice Returns true if the condition has been resolved.
    function isResolved(bytes32 conditionId) external view returns (bool);

    // -------------------------------------------------------------------------
    // ERC-1155 subset used by MarketFactory and CTFExchange
    // -------------------------------------------------------------------------

    function balanceOf(address account, uint256 id) external view returns (uint256);
    function isApprovedForAll(address account, address operator) external view returns (bool);
    function setApprovalForAll(address operator, bool approved) external;
    function safeTransferFrom(address from, address to, uint256 id, uint256 amount, bytes calldata data) external;
}
