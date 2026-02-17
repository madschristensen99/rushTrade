// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "@openzeppelin/contracts/utils/cryptography/EIP712.sol";
import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";
import "@openzeppelin/contracts/token/ERC1155/IERC1155.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import "@openzeppelin/contracts/access/Ownable2Step.sol";
import "@openzeppelin/contracts/utils/ReentrancyGuard.sol";

/// @title CTFExchange
/// @notice On-chain settlement layer for trading Gnosis CTF conditional tokens.
///
/// Architecture
/// ------------
/// This is a maker-taker, approval-based, off-chain orderbook with on-chain settlement.
///
///   Makers sign Order structs off-chain and submit them to the backend order book.
///   Takers (or the operator backend) call fillOrder() to settle on-chain.
///   Both parties must have called:
///     - collateral.approve(address(exchange), ...) for the buyer
///     - ctf.setApprovalForAll(address(exchange), true) for the seller
///
/// Order sides
/// -----------
///   BUY  – maker pays collateral and receives condition tokens.
///           makerAmount = max collateral to spend
///           takerAmount = condition tokens expected in return
///
///   SELL – maker provides condition tokens and receives collateral.
///           makerAmount = condition tokens to sell
///           takerAmount = collateral expected in return
///
/// Fill amount
/// -----------
///   fillAmount is always expressed in units of condition tokens:
///     BUY  fill: taker sends `fillAmount` tokens → maker sends proportional collateral
///     SELL fill: maker sends `fillAmount` tokens → taker sends proportional collateral
///
///   Implied price (collateral per token) = makerAmount / takerAmount  (BUY)
///                                        = takerAmount / makerAmount  (SELL)
///
/// Fees
/// ----
///   Protocol fee is charged in collateral as a basis-points rate on each fill.
///   Fee is taken from the collateral flowing to the non-fee-recipient side.
///   Max fee is capped at MAX_FEE_BPS (200 bps = 2%).
contract CTFExchange is EIP712, Ownable2Step, ReentrancyGuard {
    using SafeERC20 for IERC20;
    using ECDSA for bytes32;

    // -------------------------------------------------------------------------
    // Types
    // -------------------------------------------------------------------------

    enum Side {
        BUY,
        SELL
    }

    /// @notice A signed maker order.
    /// @param maker        Address of the order creator.
    /// @param tokenId      ERC-1155 position token ID (YES or NO position).
    /// @param makerAmount  See contract docstring.
    /// @param takerAmount  See contract docstring.
    /// @param expiration   Unix timestamp; 0 means no expiry.
    /// @param nonce        Maker-controlled nonce for cancellation / replay protection.
    /// @param feeRateBps   Maker's self-declared fee rate (must be <= MAX_FEE_BPS).
    /// @param side         BUY or SELL.
    /// @param signer       If non-zero, this address must sign instead of maker
    ///                     (useful for smart-wallet / delegate signing).
    struct Order {
        address maker;
        uint256 tokenId;
        uint256 makerAmount;
        uint256 takerAmount;
        uint256 expiration;
        uint256 nonce;
        uint256 feeRateBps;
        Side side;
        address signer;
    }

    // -------------------------------------------------------------------------
    // Constants
    // -------------------------------------------------------------------------

    bytes32 public constant ORDER_TYPEHASH = keccak256(
        "Order(address maker,uint256 tokenId,uint256 makerAmount,uint256 takerAmount,"
        "uint256 expiration,uint256 nonce,uint256 feeRateBps,uint8 side,address signer)"
    );

    uint256 public constant MAX_FEE_BPS = 200; // 2 %

    // -------------------------------------------------------------------------
    // State
    // -------------------------------------------------------------------------

    IERC1155 public immutable ctf;
    IERC20 public immutable collateral;

    address public feeRecipient;
    uint256 public protocolFeeBps;

    /// @notice orderHash => collateral-token fill amount already settled.
    ///         For BUY orders this tracks makerAmount consumed.
    ///         For SELL orders this tracks takerAmount (collateral) collected.
    ///         Either way it reaches `order.takerAmount` when fully filled.
    mapping(bytes32 => uint256) public orderFills;

    /// @notice orderHash => cancelled.
    mapping(bytes32 => bool) public cancelledOrders;

    /// @notice Addresses allowed to call fillOrders() (batch matching).
    mapping(address => bool) public operators;

    // -------------------------------------------------------------------------
    // Events
    // -------------------------------------------------------------------------

    event OrderFilled(
        bytes32 indexed orderHash,
        address indexed maker,
        address indexed taker,
        uint256 tokenId,
        uint256 makerAmountFilled,
        uint256 takerAmountFilled,
        uint256 fee
    );

    event OrderCancelled(bytes32 indexed orderHash, address indexed maker);
    event OperatorSet(address indexed operator, bool approved);
    event FeeUpdated(address indexed recipient, uint256 feeBps);

    // -------------------------------------------------------------------------
    // Constructor
    // -------------------------------------------------------------------------

    constructor(
        address _ctf,
        address _collateral,
        address _initialOwner,
        address _feeRecipient,
        uint256 _protocolFeeBps
    ) EIP712("CTFExchange", "1") Ownable(_initialOwner) {
        require(_ctf != address(0), "CTFExchange: zero CTF address");
        require(_collateral != address(0), "CTFExchange: zero collateral address");
        require(_protocolFeeBps <= MAX_FEE_BPS, "CTFExchange: protocol fee too high");

        ctf = IERC1155(_ctf);
        collateral = IERC20(_collateral);
        feeRecipient = _feeRecipient;
        protocolFeeBps = _protocolFeeBps;
    }

    // -------------------------------------------------------------------------
    // Admin
    // -------------------------------------------------------------------------

    function setOperator(address operator, bool approved) external onlyOwner {
        operators[operator] = approved;
        emit OperatorSet(operator, approved);
    }

    function setFee(address recipient, uint256 feeBps) external onlyOwner {
        require(feeBps <= MAX_FEE_BPS, "CTFExchange: fee too high");
        feeRecipient = recipient;
        protocolFeeBps = feeBps;
        emit FeeUpdated(recipient, feeBps);
    }

    // -------------------------------------------------------------------------
    // Filling
    // -------------------------------------------------------------------------

    /// @notice Fill a single maker order.
    /// @param order      The maker's signed order.
    /// @param fillAmount Tokens to fill (see contract docstring).
    /// @param signature  EIP-712 signature from order.maker (or order.signer if set).
    function fillOrder(Order calldata order, uint256 fillAmount, bytes calldata signature)
        external
        nonReentrant
    {
        _fill(order, fillAmount, msg.sender, signature);
    }

    /// @notice Batch fill multiple orders (operator only – enables backend matching).
    function fillOrders(
        Order[] calldata orders,
        uint256[] calldata fillAmounts,
        bytes[] calldata signatures
    ) external nonReentrant {
        require(
            operators[msg.sender] || msg.sender == owner(),
            "CTFExchange: not an operator"
        );
        require(
            orders.length == fillAmounts.length && orders.length == signatures.length,
            "CTFExchange: array length mismatch"
        );
        for (uint256 i = 0; i < orders.length; i++) {
            _fill(orders[i], fillAmounts[i], msg.sender, signatures[i]);
        }
    }

    // -------------------------------------------------------------------------
    // Cancellation
    // -------------------------------------------------------------------------

    /// @notice Maker cancels a single order.
    function cancelOrder(Order calldata order) external {
        require(msg.sender == order.maker, "CTFExchange: only maker can cancel");
        bytes32 h = getOrderHash(order);
        require(!cancelledOrders[h], "CTFExchange: already cancelled");
        cancelledOrders[h] = true;
        emit OrderCancelled(h, order.maker);
    }

    /// @notice Maker cancels multiple orders in one tx.
    function cancelOrders(Order[] calldata orders) external {
        for (uint256 i = 0; i < orders.length; i++) {
            require(msg.sender == orders[i].maker, "CTFExchange: only maker can cancel");
            bytes32 h = getOrderHash(orders[i]);
            if (!cancelledOrders[h]) {
                cancelledOrders[h] = true;
                emit OrderCancelled(h, orders[i].maker);
            }
        }
    }

    // -------------------------------------------------------------------------
    // Internal fill logic
    // -------------------------------------------------------------------------

    function _fill(
        Order calldata order,
        uint256 fillAmount,
        address taker,
        bytes calldata signature
    ) internal {
        require(fillAmount > 0, "CTFExchange: zero fill amount");

        bytes32 orderHash = getOrderHash(order);

        _validateOrder(order, orderHash, fillAmount, signature);

        // ------------------------------------------------------------------
        // Calculate amounts
        // ------------------------------------------------------------------
        //
        // BUY side  (maker buys tokens, taker sells tokens):
        //   fillAmount        = tokens flowing from taker → maker
        //   collateralFilled  = collateral flowing from maker → taker
        //                     = fillAmount * makerAmount / takerAmount
        //
        // SELL side (maker sells tokens, taker buys tokens):
        //   fillAmount        = tokens flowing from maker → taker
        //   collateralFilled  = collateral flowing from taker → maker
        //                     = fillAmount * takerAmount / makerAmount
        //
        uint256 collateralFilled;
        if (order.side == Side.BUY) {
            collateralFilled = (fillAmount * order.makerAmount) / order.takerAmount;
        } else {
            collateralFilled = (fillAmount * order.takerAmount) / order.makerAmount;
        }

        // Protocol fee taken from the collateral leg.
        uint256 fee = (collateralFilled * protocolFeeBps) / 10_000;

        // Accumulate fills (track in token units for both sides for consistency).
        orderFills[orderHash] += fillAmount;

        // ------------------------------------------------------------------
        // Execute transfers
        // ------------------------------------------------------------------

        if (order.side == Side.BUY) {
            // Taker sends tokens to maker.
            ctf.safeTransferFrom(taker, order.maker, order.tokenId, fillAmount, "");

            // Maker pays collateral to taker (net of fee).
            collateral.safeTransferFrom(order.maker, taker, collateralFilled - fee);

            // Fee to protocol.
            if (fee > 0) {
                collateral.safeTransferFrom(order.maker, feeRecipient, fee);
            }
        } else {
            // Maker sends tokens to taker.
            ctf.safeTransferFrom(order.maker, taker, order.tokenId, fillAmount, "");

            // Taker pays collateral to maker (net of fee).
            collateral.safeTransferFrom(taker, order.maker, collateralFilled - fee);

            // Fee to protocol.
            if (fee > 0) {
                collateral.safeTransferFrom(taker, feeRecipient, fee);
            }
        }

        emit OrderFilled(
            orderHash,
            order.maker,
            taker,
            order.tokenId,
            order.side == Side.SELL ? fillAmount : collateralFilled,
            order.side == Side.BUY ? fillAmount : collateralFilled,
            fee
        );
    }

    function _validateOrder(
        Order calldata order,
        bytes32 orderHash,
        uint256 fillAmount,
        bytes calldata signature
    ) internal view {
        require(!cancelledOrders[orderHash], "CTFExchange: order cancelled");
        require(
            order.expiration == 0 || block.timestamp <= order.expiration,
            "CTFExchange: order expired"
        );
        require(order.feeRateBps <= MAX_FEE_BPS, "CTFExchange: maker fee rate too high");
        require(order.makerAmount > 0 && order.takerAmount > 0, "CTFExchange: zero order amounts");

        // Cap fill to remaining available amount.
        uint256 filled = orderFills[orderHash];
        uint256 remaining = order.side == Side.BUY
            ? order.takerAmount - filled  // remaining tokens the maker will accept
            : order.makerAmount - filled; // remaining tokens the maker will sell
        require(fillAmount <= remaining, "CTFExchange: fill exceeds remaining order");

        // Signature check.
        address expectedSigner = order.signer != address(0) ? order.signer : order.maker;
        address recovered = _hashTypedDataV4(orderHash).recover(signature);
        require(recovered == expectedSigner, "CTFExchange: invalid signature");
    }

    // -------------------------------------------------------------------------
    // Views
    // -------------------------------------------------------------------------

    /// @notice EIP-712 hash of an order (used for signing off-chain).
    function getOrderHash(Order calldata order) public view returns (bytes32) {
        return _hashTypedDataV4(
            keccak256(
                abi.encode(
                    ORDER_TYPEHASH,
                    order.maker,
                    order.tokenId,
                    order.makerAmount,
                    order.takerAmount,
                    order.expiration,
                    order.nonce,
                    order.feeRateBps,
                    order.side,
                    order.signer
                )
            )
        );
    }

    /// @notice Remaining fillable token amount for an order.
    function getRemainingAmount(Order calldata order) external view returns (uint256) {
        bytes32 h = getOrderHash(order);
        if (cancelledOrders[h]) return 0;
        uint256 filled = orderFills[h];
        uint256 total = order.side == Side.BUY ? order.takerAmount : order.makerAmount;
        return filled >= total ? 0 : total - filled;
    }

    /// @notice Whether an order is live (not cancelled and not fully filled).
    function isOrderLive(Order calldata order) external view returns (bool) {
        bytes32 h = getOrderHash(order);
        if (cancelledOrders[h]) return false;
        if (order.expiration > 0 && block.timestamp > order.expiration) return false;
        uint256 filled = orderFills[h];
        uint256 total = order.side == Side.BUY ? order.takerAmount : order.makerAmount;
        return filled < total;
    }

    /// @notice EIP-712 domain separator (useful for off-chain signing helpers).
    function domainSeparator() external view returns (bytes32) {
        return _domainSeparatorV4();
    }
}
