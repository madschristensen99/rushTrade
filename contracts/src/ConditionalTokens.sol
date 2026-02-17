// SPDX-License-Identifier: LGPL-3.0
// Canonical Gnosis Conditional Token Framework implementation.
// Ported to Solidity 0.8 from the original at:
// https://github.com/gnosis/conditional-tokens-contracts
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/token/ERC1155/ERC1155.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";

/// @title ConditionalTokens
/// @notice ERC-1155 token contract representing conditional positions in prediction markets.
///         Binary markets (YES/NO) use outcomeSlotCount = 2.
///
/// Position IDs
/// ------------
/// A position is identified by its collateral token and a collection ID.
/// A collection ID is built by XOR-ing a parent collection ID with the hash
/// of a (conditionId, indexSet) pair, allowing nested/scalar markets in future.
/// For a top-level binary market the parent collection is bytes32(0).
///
/// Splitting / Merging / Redeeming
/// ---------------------------------
///   splitPosition  – lock collateral (or a parent position) and mint outcome tokens
///   mergePositions – burn outcome tokens and release collateral (reverse of split)
///   redeemPositions – after resolution, burn winning positions and claim collateral
contract ConditionalTokens is ERC1155 {
    using SafeERC20 for IERC20;

    // -------------------------------------------------------------------------
    // Storage
    // -------------------------------------------------------------------------

    /// @notice conditionId => oracle that will report payouts
    mapping(bytes32 => address) public oracles;

    /// @notice conditionId => number of outcome slots (2 for binary markets)
    mapping(bytes32 => uint256) public outcomeSlotCounts;

    /// @notice conditionId => payout numerators (set on resolution)
    mapping(bytes32 => uint256[]) public payoutNumerators;

    /// @notice conditionId => sum of payout numerators (non-zero iff resolved)
    mapping(bytes32 => uint256) public payoutDenominator;

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
    // Constructor
    // -------------------------------------------------------------------------

    constructor() ERC1155("") {}

    // -------------------------------------------------------------------------
    // Core functions
    // -------------------------------------------------------------------------

    /// @notice Register a new condition so that positions can be split against it.
    /// @param oracle       Address that will call reportPayouts for this condition.
    /// @param questionId   Unique identifier for the question (e.g. keccak of market title + salt).
    /// @param outcomeSlotCount  Number of mutually exclusive outcomes (2 for YES/NO).
    function prepareCondition(address oracle, bytes32 questionId, uint256 outcomeSlotCount) external {
        require(outcomeSlotCount > 1, "CTF: need >1 outcome slots");
        require(outcomeSlotCount <= 256, "CTF: too many outcome slots");

        bytes32 conditionId = getConditionId(oracle, questionId, outcomeSlotCount);
        require(outcomeSlotCounts[conditionId] == 0, "CTF: condition already prepared");

        outcomeSlotCounts[conditionId] = outcomeSlotCount;
        oracles[conditionId] = oracle;

        emit ConditionPreparation(conditionId, oracle, questionId, outcomeSlotCount);
    }

    /// @notice Called by the oracle to report the outcome of a condition.
    ///         For a binary YES/NO market that resolved YES: payouts = [1, 0].
    ///         For NO: payouts = [0, 1].  For invalid/draw: [1, 1].
    /// @param questionId  Same questionId used during prepareCondition.
    /// @param payouts     Payout numerators, one per outcome slot.
    function reportPayouts(bytes32 questionId, uint256[] calldata payouts) external {
        uint256 n = payouts.length;
        require(n > 1, "CTF: need >1 outcome slots");

        bytes32 conditionId = getConditionId(msg.sender, questionId, n);
        require(outcomeSlotCounts[conditionId] == n, "CTF: condition not prepared or slot count mismatch");
        require(payoutDenominator[conditionId] == 0, "CTF: condition already resolved");

        uint256 den = 0;
        for (uint256 i = 0; i < n; i++) {
            den += payouts[i];
        }
        require(den > 0, "CTF: all-zero payouts");

        payoutNumerators[conditionId] = payouts;
        payoutDenominator[conditionId] = den;

        emit ConditionResolution(conditionId, msg.sender, questionId, n, payouts);
    }

    /// @notice Lock collateral (or a parent position) and mint a set of outcome tokens.
    /// @param collateralToken     ERC-20 used as collateral.
    /// @param parentCollectionId  bytes32(0) for a top-level split.
    /// @param conditionId         The condition to split against.
    /// @param partition           Disjoint, non-empty index sets covering all wanted outcomes.
    /// @param amount              How many tokens to mint per partition slot (1e18 scale typical).
    function splitPosition(
        IERC20 collateralToken,
        bytes32 parentCollectionId,
        bytes32 conditionId,
        uint256[] calldata partition,
        uint256 amount
    ) external {
        require(partition.length > 1, "CTF: empty or singleton partition");
        uint256 slotCount = outcomeSlotCounts[conditionId];
        require(slotCount > 0, "CTF: condition not prepared");

        uint256 fullIndexSet = (1 << slotCount) - 1;
        uint256 freeIndexSet = fullIndexSet;

        uint256[] memory positionIds = new uint256[](partition.length);
        uint256[] memory amounts = new uint256[](partition.length);

        for (uint256 i = 0; i < partition.length; i++) {
            uint256 indexSet = partition[i];
            require(indexSet > 0 && indexSet < (1 << slotCount), "CTF: invalid index set");
            require((indexSet & freeIndexSet) == indexSet, "CTF: partition not disjoint");
            freeIndexSet ^= indexSet;

            bytes32 collectionId = getCollectionId(parentCollectionId, conditionId, indexSet);
            positionIds[i] = getPositionId(collateralToken, collectionId);
            amounts[i] = amount;
        }

        if (parentCollectionId == bytes32(0)) {
            collateralToken.safeTransferFrom(msg.sender, address(this), amount);
        } else {
            _burn(msg.sender, getPositionId(collateralToken, parentCollectionId), amount);
        }

        _mintBatch(msg.sender, positionIds, amounts, "");

        emit PositionSplit(msg.sender, collateralToken, parentCollectionId, conditionId, partition, amount);
    }

    /// @notice Burn a set of outcome tokens and release collateral (reverse of splitPosition).
    function mergePositions(
        IERC20 collateralToken,
        bytes32 parentCollectionId,
        bytes32 conditionId,
        uint256[] calldata partition,
        uint256 amount
    ) external {
        require(partition.length > 1, "CTF: empty or singleton partition");
        uint256 slotCount = outcomeSlotCounts[conditionId];
        require(slotCount > 0, "CTF: condition not prepared");

        uint256[] memory positionIds = new uint256[](partition.length);
        uint256[] memory amounts = new uint256[](partition.length);

        for (uint256 i = 0; i < partition.length; i++) {
            uint256 indexSet = partition[i];
            require(indexSet > 0 && indexSet < (1 << slotCount), "CTF: invalid index set");

            bytes32 collectionId = getCollectionId(parentCollectionId, conditionId, indexSet);
            positionIds[i] = getPositionId(collateralToken, collectionId);
            amounts[i] = amount;
        }

        _burnBatch(msg.sender, positionIds, amounts);

        if (parentCollectionId == bytes32(0)) {
            collateralToken.safeTransfer(msg.sender, amount);
        } else {
            _mint(msg.sender, getPositionId(collateralToken, parentCollectionId), amount, "");
        }

        emit PositionsMerge(msg.sender, collateralToken, parentCollectionId, conditionId, partition, amount);
    }

    /// @notice After a condition is resolved, burn winning positions and claim collateral.
    /// @param collateralToken     ERC-20 used as collateral.
    /// @param parentCollectionId  bytes32(0) for top-level positions.
    /// @param conditionId         The resolved condition.
    /// @param indexSets           Which positions the caller holds and wants to redeem.
    function redeemPositions(
        IERC20 collateralToken,
        bytes32 parentCollectionId,
        bytes32 conditionId,
        uint256[] calldata indexSets
    ) external {
        uint256 den = payoutDenominator[conditionId];
        require(den > 0, "CTF: condition not yet resolved");

        uint256 slotCount = outcomeSlotCounts[conditionId];
        uint256 totalPayout = 0;

        for (uint256 i = 0; i < indexSets.length; i++) {
            uint256 indexSet = indexSets[i];
            require(indexSet > 0 && indexSet < (1 << slotCount), "CTF: invalid index set");

            bytes32 collectionId = getCollectionId(parentCollectionId, conditionId, indexSet);
            uint256 positionId = getPositionId(collateralToken, collectionId);
            uint256 balance = balanceOf(msg.sender, positionId);

            if (balance > 0) {
                _burn(msg.sender, positionId, balance);

                uint256 positionPayout = 0;
                for (uint256 j = 0; j < slotCount; j++) {
                    if (indexSet & (1 << j) != 0) {
                        positionPayout += payoutNumerators[conditionId][j] * balance;
                    }
                }
                totalPayout += positionPayout;
            }
        }

        if (totalPayout > 0) {
            totalPayout /= den;
            if (parentCollectionId == bytes32(0)) {
                collateralToken.safeTransfer(msg.sender, totalPayout);
            } else {
                _mint(msg.sender, getPositionId(collateralToken, parentCollectionId), totalPayout, "");
            }
        }

        emit PayoutRedemption(msg.sender, collateralToken, parentCollectionId, conditionId, indexSets, totalPayout);
    }

    // -------------------------------------------------------------------------
    // Pure helpers – deterministic IDs
    // -------------------------------------------------------------------------

    /// @notice Unique ID for a (oracle, questionId, outcomeSlotCount) tuple.
    function getConditionId(address oracle, bytes32 questionId, uint256 outcomeSlotCount)
        public
        pure
        returns (bytes32)
    {
        return keccak256(abi.encodePacked(oracle, questionId, outcomeSlotCount));
    }

    /// @notice Unique ID for a collection (parent XOR outcome hash).
    function getCollectionId(bytes32 parentCollectionId, bytes32 conditionId, uint256 indexSet)
        public
        pure
        returns (bytes32)
    {
        return bytes32(uint256(parentCollectionId) ^ uint256(keccak256(abi.encodePacked(conditionId, indexSet))));
    }

    /// @notice ERC-1155 token ID for a position (collateral + collection).
    function getPositionId(IERC20 collateralToken, bytes32 collectionId) public pure returns (uint256) {
        return uint256(keccak256(abi.encodePacked(collateralToken, collectionId)));
    }

    /// @notice Convenience: get the YES and NO position IDs for a top-level binary market.
    /// @param collateral  Collateral token address.
    /// @param conditionId The condition ID for the market.
    /// @return yesPositionId  Position ID for indexSet = 0b01 (outcome 0, "YES").
    /// @return noPositionId   Position ID for indexSet = 0b10 (outcome 1, "NO").
    function getPositionIds(IERC20 collateral, bytes32 conditionId)
        external
        pure
        returns (uint256 yesPositionId, uint256 noPositionId)
    {
        yesPositionId = getPositionId(collateral, getCollectionId(bytes32(0), conditionId, 1));
        noPositionId = getPositionId(collateral, getCollectionId(bytes32(0), conditionId, 2));
    }

    /// @notice Whether a condition has been resolved.
    function isResolved(bytes32 conditionId) external view returns (bool) {
        return payoutDenominator[conditionId] > 0;
    }
}
