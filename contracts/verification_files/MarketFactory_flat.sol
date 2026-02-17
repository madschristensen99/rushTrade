// SPDX-License-Identifier: MIT
pragma solidity >=0.4.16 ^0.8.20 ^0.8.24;

// lib/openzeppelin-contracts/contracts/utils/Context.sol

// OpenZeppelin Contracts (last updated v5.0.1) (utils/Context.sol)

/**
 * @dev Provides information about the current execution context, including the
 * sender of the transaction and its data. While these are generally available
 * via msg.sender and msg.data, they should not be accessed in such a direct
 * manner, since when dealing with meta-transactions the account sending and
 * paying for execution may not be the actual sender (as far as an application
 * is concerned).
 *
 * This contract is only required for intermediate, library-like contracts.
 */
abstract contract Context {
    function _msgSender() internal view virtual returns (address) {
        return msg.sender;
    }

    function _msgData() internal view virtual returns (bytes calldata) {
        return msg.data;
    }

    function _contextSuffixLength() internal view virtual returns (uint256) {
        return 0;
    }
}

// lib/openzeppelin-contracts/contracts/token/ERC20/IERC20.sol

// OpenZeppelin Contracts (last updated v5.4.0) (token/ERC20/IERC20.sol)

/**
 * @dev Interface of the ERC-20 standard as defined in the ERC.
 */
interface IERC20 {
    /**
     * @dev Emitted when `value` tokens are moved from one account (`from`) to
     * another (`to`).
     *
     * Note that `value` may be zero.
     */
    event Transfer(address indexed from, address indexed to, uint256 value);

    /**
     * @dev Emitted when the allowance of a `spender` for an `owner` is set by
     * a call to {approve}. `value` is the new allowance.
     */
    event Approval(address indexed owner, address indexed spender, uint256 value);

    /**
     * @dev Returns the value of tokens in existence.
     */
    function totalSupply() external view returns (uint256);

    /**
     * @dev Returns the value of tokens owned by `account`.
     */
    function balanceOf(address account) external view returns (uint256);

    /**
     * @dev Moves a `value` amount of tokens from the caller's account to `to`.
     *
     * Returns a boolean value indicating whether the operation succeeded.
     *
     * Emits a {Transfer} event.
     */
    function transfer(address to, uint256 value) external returns (bool);

    /**
     * @dev Returns the remaining number of tokens that `spender` will be
     * allowed to spend on behalf of `owner` through {transferFrom}. This is
     * zero by default.
     *
     * This value changes when {approve} or {transferFrom} are called.
     */
    function allowance(address owner, address spender) external view returns (uint256);

    /**
     * @dev Sets a `value` amount of tokens as the allowance of `spender` over the
     * caller's tokens.
     *
     * Returns a boolean value indicating whether the operation succeeded.
     *
     * IMPORTANT: Beware that changing an allowance with this method brings the risk
     * that someone may use both the old and the new allowance by unfortunate
     * transaction ordering. One possible solution to mitigate this race
     * condition is to first reduce the spender's allowance to 0 and set the
     * desired value afterwards:
     * https://github.com/ethereum/EIPs/issues/20#issuecomment-263524729
     *
     * Emits an {Approval} event.
     */
    function approve(address spender, uint256 value) external returns (bool);

    /**
     * @dev Moves a `value` amount of tokens from `from` to `to` using the
     * allowance mechanism. `value` is then deducted from the caller's
     * allowance.
     *
     * Returns a boolean value indicating whether the operation succeeded.
     *
     * Emits a {Transfer} event.
     */
    function transferFrom(address from, address to, uint256 value) external returns (bool);
}

// src/interfaces/IConditionalTokens.sol

/// @title IConditionalTokens
/// @notice Interface for the Gnosis Conditional Token Framework used by
///         MarketFactory and CTFExchange to interact with the CTF contract.
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
    // Core
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
    // Views
    // -------------------------------------------------------------------------

    function oracles(bytes32 conditionId) external view returns (address);
    function outcomeSlotCounts(bytes32 conditionId) external view returns (uint256);
    function payoutNumerators(bytes32 conditionId, uint256 index) external view returns (uint256);
    function payoutDenominator(bytes32 conditionId) external view returns (uint256);
    function isResolved(bytes32 conditionId) external view returns (bool);

    function balanceOf(address account, uint256 id) external view returns (uint256);
    function isApprovedForAll(address account, address operator) external view returns (bool);
    function setApprovalForAll(address operator, bool approved) external;

    // -------------------------------------------------------------------------
    // Pure helpers
    // -------------------------------------------------------------------------

    function getConditionId(address oracle, bytes32 questionId, uint256 outcomeSlotCount)
        external
        pure
        returns (bytes32);

    function getCollectionId(bytes32 parentCollectionId, bytes32 conditionId, uint256 indexSet)
        external
        pure
        returns (bytes32);

    function getPositionId(IERC20 collateralToken, bytes32 collectionId) external pure returns (uint256);

    function getPositionIds(IERC20 collateral, bytes32 conditionId)
        external
        pure
        returns (uint256 yesPositionId, uint256 noPositionId);
}

// lib/openzeppelin-contracts/contracts/access/Ownable.sol

// OpenZeppelin Contracts (last updated v5.0.0) (access/Ownable.sol)

/**
 * @dev Contract module which provides a basic access control mechanism, where
 * there is an account (an owner) that can be granted exclusive access to
 * specific functions.
 *
 * The initial owner is set to the address provided by the deployer. This can
 * later be changed with {transferOwnership}.
 *
 * This module is used through inheritance. It will make available the modifier
 * `onlyOwner`, which can be applied to your functions to restrict their use to
 * the owner.
 */
abstract contract Ownable is Context {
    address private _owner;

    /**
     * @dev The caller account is not authorized to perform an operation.
     */
    error OwnableUnauthorizedAccount(address account);

    /**
     * @dev The owner is not a valid owner account. (eg. `address(0)`)
     */
    error OwnableInvalidOwner(address owner);

    event OwnershipTransferred(address indexed previousOwner, address indexed newOwner);

    /**
     * @dev Initializes the contract setting the address provided by the deployer as the initial owner.
     */
    constructor(address initialOwner) {
        if (initialOwner == address(0)) {
            revert OwnableInvalidOwner(address(0));
        }
        _transferOwnership(initialOwner);
    }

    /**
     * @dev Throws if called by any account other than the owner.
     */
    modifier onlyOwner() {
        _checkOwner();
        _;
    }

    /**
     * @dev Returns the address of the current owner.
     */
    function owner() public view virtual returns (address) {
        return _owner;
    }

    /**
     * @dev Throws if the sender is not the owner.
     */
    function _checkOwner() internal view virtual {
        if (owner() != _msgSender()) {
            revert OwnableUnauthorizedAccount(_msgSender());
        }
    }

    /**
     * @dev Leaves the contract without owner. It will not be possible to call
     * `onlyOwner` functions. Can only be called by the current owner.
     *
     * NOTE: Renouncing ownership will leave the contract without an owner,
     * thereby disabling any functionality that is only available to the owner.
     */
    function renounceOwnership() public virtual onlyOwner {
        _transferOwnership(address(0));
    }

    /**
     * @dev Transfers ownership of the contract to a new account (`newOwner`).
     * Can only be called by the current owner.
     */
    function transferOwnership(address newOwner) public virtual onlyOwner {
        if (newOwner == address(0)) {
            revert OwnableInvalidOwner(address(0));
        }
        _transferOwnership(newOwner);
    }

    /**
     * @dev Transfers ownership of the contract to a new account (`newOwner`).
     * Internal function without access restriction.
     */
    function _transferOwnership(address newOwner) internal virtual {
        address oldOwner = _owner;
        _owner = newOwner;
        emit OwnershipTransferred(oldOwner, newOwner);
    }
}

// lib/openzeppelin-contracts/contracts/access/Ownable2Step.sol

// OpenZeppelin Contracts (last updated v5.1.0) (access/Ownable2Step.sol)

/**
 * @dev Contract module which provides access control mechanism, where
 * there is an account (an owner) that can be granted exclusive access to
 * specific functions.
 *
 * This extension of the {Ownable} contract includes a two-step mechanism to transfer
 * ownership, where the new owner must call {acceptOwnership} in order to replace the
 * old one. This can help prevent common mistakes, such as transfers of ownership to
 * incorrect accounts, or to contracts that are unable to interact with the
 * permission system.
 *
 * The initial owner is specified at deployment time in the constructor for `Ownable`. This
 * can later be changed with {transferOwnership} and {acceptOwnership}.
 *
 * This module is used through inheritance. It will make available all functions
 * from parent (Ownable).
 */
abstract contract Ownable2Step is Ownable {
    address private _pendingOwner;

    event OwnershipTransferStarted(address indexed previousOwner, address indexed newOwner);

    /**
     * @dev Returns the address of the pending owner.
     */
    function pendingOwner() public view virtual returns (address) {
        return _pendingOwner;
    }

    /**
     * @dev Starts the ownership transfer of the contract to a new account. Replaces the pending transfer if there is one.
     * Can only be called by the current owner.
     *
     * Setting `newOwner` to the zero address is allowed; this can be used to cancel an initiated ownership transfer.
     */
    function transferOwnership(address newOwner) public virtual override onlyOwner {
        _pendingOwner = newOwner;
        emit OwnershipTransferStarted(owner(), newOwner);
    }

    /**
     * @dev Transfers ownership of the contract to a new account (`newOwner`) and deletes any pending owner.
     * Internal function without access restriction.
     */
    function _transferOwnership(address newOwner) internal virtual override {
        delete _pendingOwner;
        super._transferOwnership(newOwner);
    }

    /**
     * @dev The new owner accepts the ownership transfer.
     */
    function acceptOwnership() public virtual {
        address sender = _msgSender();
        if (pendingOwner() != sender) {
            revert OwnableUnauthorizedAccount(sender);
        }
        _transferOwnership(sender);
    }
}

// src/MarketFactory.sol

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
        conditionId = ctf.getConditionId(oracle, questionId, 2);
        require(markets[conditionId].oracle == address(0), "MarketFactory: market already exists");

        // Prepare the condition on the CTF (noop if already prepared externally).
        ctf.prepareCondition(oracle, questionId, 2);

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

    /// @notice Convenience: get the YES/NO ERC-1155 position IDs for a market.
    function getPositionIds(bytes32 conditionId)
        external
        view
        returns (uint256 yesId, uint256 noId)
    {
        Market storage m = markets[conditionId];
        require(m.oracle != address(0), "MarketFactory: market does not exist");
        (yesId, noId) = ctf.getPositionIds(IERC20(m.collateralToken), conditionId);
    }
}

