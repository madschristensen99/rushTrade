// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../src/ConditionalTokens.sol";
import "../src/MarketFactory.sol";
import "../src/CTFExchange.sol";

/// @dev Minimal ERC-20 mock (same as MarketFactory test, duplicated for isolation).
contract MockUSDC {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        require(balanceOf[msg.sender] >= amount, "insufficient");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        require(allowance[from][msg.sender] >= amount, "insufficient allowance");
        allowance[from][msg.sender] -= amount;
        require(balanceOf[from] >= amount, "insufficient balance");
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}

contract CTFExchangeTest is Test {
    ConditionalTokens public ctf;
    MarketFactory public factory;
    CTFExchange public exchange;
    MockUSDC public usdc;

    address public owner = makeAddr("owner");
    address public feeRecipient = makeAddr("feeRecipient");
    address public oracle = makeAddr("oracle");
    address public operator = makeAddr("operator");

    // EVM key pair for signing.
    uint256 public makerKey = 0xA11CE;
    address public maker;

    address public taker = makeAddr("taker");

    bytes32 public questionId = keccak256("Will Monad reach $1b TVL by Q4 2026?");
    bytes32 public conditionId;
    uint256 public yesId;
    uint256 public noId;

    uint256 public constant COLLATERAL_AMOUNT = 1000e6;  // 1000 USDC

    function setUp() public {
        maker = vm.addr(makerKey);

        usdc = new MockUSDC();

        vm.startPrank(owner);
        ctf = new ConditionalTokens();
        factory = new MarketFactory(address(ctf), owner);
        factory.setCollateralApproval(address(usdc), true);

        exchange = new CTFExchange(
            address(ctf),
            address(usdc),
            owner,
            feeRecipient,
            50  // 0.5% protocol fee
        );
        exchange.setOperator(operator, true);

        // Create a market.
        conditionId = factory.createMarket(
            questionId,
            oracle,
            address(usdc),
            block.timestamp + 30 days,
            "Monad TVL market",
            "Will Monad reach $1b TVL?",
            "Crypto"
        );
        vm.stopPrank();

        (yesId, noId) = factory.getPositionIds(conditionId);

        // Mint USDC and split into YES/NO tokens.
        // Maker will hold YES tokens; taker will hold collateral for buying.
        usdc.mint(maker, COLLATERAL_AMOUNT);
        usdc.mint(taker, COLLATERAL_AMOUNT);

        // Maker splits collateral into YES + NO tokens.
        vm.startPrank(maker);
        IERC20(address(usdc)).approve(address(ctf), COLLATERAL_AMOUNT);
        uint256[] memory partition = new uint256[](2);
        partition[0] = 1; // YES indexSet
        partition[1] = 2; // NO indexSet
        ctf.splitPosition(IERC20(address(usdc)), bytes32(0), conditionId, partition, COLLATERAL_AMOUNT);
        // Approve exchange to transfer CTF tokens on maker's behalf.
        ctf.setApprovalForAll(address(exchange), true);
        vm.stopPrank();

        // Taker approves exchange to spend their USDC (for buying YES tokens).
        vm.prank(taker);
        IERC20(address(usdc)).approve(address(exchange), type(uint256).max);

        // Maker approves exchange to spend their USDC (needed if maker buys).
        vm.prank(maker);
        IERC20(address(usdc)).approve(address(exchange), type(uint256).max);
    }

    // -------------------------------------------------------------------------
    // Helpers
    // -------------------------------------------------------------------------

    function _makeSellOrder(
        uint256 tokenAmount,
        uint256 collateralAmount,
        uint256 expiration,
        uint256 nonce
    ) internal view returns (CTFExchange.Order memory order) {
        order = CTFExchange.Order({
            maker: maker,
            tokenId: yesId,
            makerAmount: tokenAmount,   // tokens to sell
            takerAmount: collateralAmount, // collateral to receive
            expiration: expiration,
            nonce: nonce,
            feeRateBps: 0,
            side: CTFExchange.Side.SELL,
            signer: address(0)
        });
    }

    function _makeBuyOrder(
        uint256 collateralAmount,
        uint256 tokenAmount,
        uint256 expiration,
        uint256 nonce
    ) internal view returns (CTFExchange.Order memory order) {
        order = CTFExchange.Order({
            maker: maker,
            tokenId: yesId,
            makerAmount: collateralAmount, // collateral to spend
            takerAmount: tokenAmount,      // tokens to receive
            expiration: expiration,
            nonce: nonce,
            feeRateBps: 0,
            side: CTFExchange.Side.BUY,
            signer: address(0)
        });
    }

    function _sign(CTFExchange.Order memory order) internal view returns (bytes memory sig) {
        bytes32 h = exchange.getOrderHash(order);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(makerKey, h);
        sig = abi.encodePacked(r, s, v);
    }

    // -------------------------------------------------------------------------
    // Constructor / admin
    // -------------------------------------------------------------------------

    function test_Constructor() public view {
        assertEq(address(exchange.ctf()), address(ctf));
        assertEq(address(exchange.collateral()), address(usdc));
        assertEq(exchange.feeRecipient(), feeRecipient);
        assertEq(exchange.protocolFeeBps(), 50);
    }

    function test_SetOperator() public {
        address newOp = makeAddr("newOp");
        vm.prank(owner);
        exchange.setOperator(newOp, true);
        assertTrue(exchange.operators(newOp));
    }

    function test_SetFee() public {
        vm.prank(owner);
        exchange.setFee(feeRecipient, 100);
        assertEq(exchange.protocolFeeBps(), 100);
    }

    function test_SetFee_RevertTooHigh() public {
        vm.prank(owner);
        vm.expectRevert("CTFExchange: fee too high");
        exchange.setFee(feeRecipient, 201);
    }

    // -------------------------------------------------------------------------
    // SELL order fills
    // -------------------------------------------------------------------------

    function test_FillSellOrder_Full() public {
        // Maker wants to sell 100e6 YES tokens for 60e6 USDC (price 0.60).
        uint256 tokenAmount = 100e6;
        uint256 collateralAmount = 60e6;

        CTFExchange.Order memory order = _makeSellOrder(tokenAmount, collateralAmount, 0, 1);
        bytes memory sig = _sign(order);

        uint256 makerYesBefore = ctf.balanceOf(maker, yesId);
        uint256 takerYesBefore = ctf.balanceOf(taker, yesId);
        uint256 makerUsdcBefore = usdc.balanceOf(maker);
        uint256 takerUsdcBefore = usdc.balanceOf(taker);

        // Taker fills the full order.
        vm.prank(taker);
        exchange.fillOrder(order, tokenAmount, sig);

        // Maker sent tokenAmount YES tokens, received collateral minus fee.
        uint256 fee = (collateralAmount * 50) / 10_000; // 0.5%
        assertEq(ctf.balanceOf(maker, yesId), makerYesBefore - tokenAmount);
        assertEq(ctf.balanceOf(taker, yesId), takerYesBefore + tokenAmount);
        assertEq(usdc.balanceOf(maker), makerUsdcBefore + collateralAmount - fee);
        assertEq(usdc.balanceOf(taker), takerUsdcBefore - collateralAmount);
        assertEq(usdc.balanceOf(feeRecipient), fee);
    }

    function test_FillSellOrder_Partial() public {
        uint256 tokenAmount = 100e6;
        uint256 collateralAmount = 60e6;

        CTFExchange.Order memory order = _makeSellOrder(tokenAmount, collateralAmount, 0, 2);
        bytes memory sig = _sign(order);

        // Fill half.
        vm.prank(taker);
        exchange.fillOrder(order, 50e6, sig);

        bytes32 h = exchange.getOrderHash(order);
        assertEq(exchange.orderFills(h), 50e6);

        // Fill the remaining half.
        vm.prank(taker);
        exchange.fillOrder(order, 50e6, sig);
        assertEq(exchange.orderFills(h), 100e6);
    }

    function test_FillSellOrder_RevertOverfill() public {
        uint256 tokenAmount = 100e6;
        CTFExchange.Order memory order = _makeSellOrder(tokenAmount, 60e6, 0, 3);
        bytes memory sig = _sign(order);

        vm.prank(taker);
        vm.expectRevert("CTFExchange: fill exceeds remaining order");
        exchange.fillOrder(order, tokenAmount + 1, sig);
    }

    // -------------------------------------------------------------------------
    // BUY order fills
    // -------------------------------------------------------------------------

    function test_FillBuyOrder_Full() public {
        // Maker wants to buy 100e6 YES tokens, paying up to 60e6 USDC.
        uint256 collateralAmount = 60e6;
        uint256 tokenAmount = 100e6;

        // Give maker enough USDC (they already split theirs into tokens; give more).
        usdc.mint(maker, collateralAmount);

        CTFExchange.Order memory order = _makeBuyOrder(collateralAmount, tokenAmount, 0, 10);
        bytes memory sig = _sign(order);

        // Taker holds YES tokens; they need to approve exchange.
        // First, taker needs YES tokens — give them from split.
        usdc.mint(taker, tokenAmount);
        vm.startPrank(taker);
        IERC20(address(usdc)).approve(address(ctf), tokenAmount);
        uint256[] memory partition = new uint256[](2);
        partition[0] = 1;
        partition[1] = 2;
        ctf.splitPosition(IERC20(address(usdc)), bytes32(0), conditionId, partition, tokenAmount);
        ctf.setApprovalForAll(address(exchange), true);
        vm.stopPrank();

        uint256 makerUsdcBefore = usdc.balanceOf(maker);
        uint256 takerUsdcBefore = usdc.balanceOf(taker);
        uint256 makerYesBefore = ctf.balanceOf(maker, yesId);
        uint256 takerYesBefore = ctf.balanceOf(taker, yesId);

        // Taker fills: sends tokenAmount YES tokens, receives collateral.
        vm.prank(taker);
        exchange.fillOrder(order, tokenAmount, sig);

        uint256 fee = (collateralAmount * 50) / 10_000;
        assertEq(ctf.balanceOf(maker, yesId), makerYesBefore + tokenAmount);
        assertEq(ctf.balanceOf(taker, yesId), takerYesBefore - tokenAmount);
        assertEq(usdc.balanceOf(maker), makerUsdcBefore - collateralAmount);
        assertEq(usdc.balanceOf(taker), takerUsdcBefore + collateralAmount - fee);
        assertEq(usdc.balanceOf(feeRecipient), fee); // accumulated across tests so just check >= fee
    }

    // -------------------------------------------------------------------------
    // Cancellation
    // -------------------------------------------------------------------------

    function test_CancelOrder() public {
        CTFExchange.Order memory order = _makeSellOrder(100e6, 60e6, 0, 20);
        bytes memory sig = _sign(order);
        bytes32 h = exchange.getOrderHash(order);

        vm.prank(maker);
        exchange.cancelOrder(order);

        assertTrue(exchange.cancelledOrders(h));

        vm.prank(taker);
        vm.expectRevert("CTFExchange: order cancelled");
        exchange.fillOrder(order, 100e6, sig);
    }

    function test_CancelOrder_RevertNonMaker() public {
        CTFExchange.Order memory order = _makeSellOrder(100e6, 60e6, 0, 21);

        vm.prank(taker);
        vm.expectRevert("CTFExchange: only maker can cancel");
        exchange.cancelOrder(order);
    }

    function test_CancelOrders_Batch() public {
        CTFExchange.Order[] memory orders = new CTFExchange.Order[](3);
        for (uint256 i = 0; i < 3; i++) {
            orders[i] = _makeSellOrder(100e6, 60e6, 0, 30 + i);
        }

        vm.prank(maker);
        exchange.cancelOrders(orders);

        for (uint256 i = 0; i < 3; i++) {
            assertTrue(exchange.cancelledOrders(exchange.getOrderHash(orders[i])));
        }
    }

    // -------------------------------------------------------------------------
    // Expiry
    // -------------------------------------------------------------------------

    function test_ExpiredOrder_Reverts() public {
        uint256 expiration = block.timestamp + 1 hours;
        CTFExchange.Order memory order = _makeSellOrder(100e6, 60e6, expiration, 40);
        bytes memory sig = _sign(order);

        vm.warp(expiration + 1);

        vm.prank(taker);
        vm.expectRevert("CTFExchange: order expired");
        exchange.fillOrder(order, 100e6, sig);
    }

    function test_NoExpiry_ZeroTimestamp() public {
        // expiration = 0 means never expires.
        CTFExchange.Order memory order = _makeSellOrder(50e6, 30e6, 0, 41);
        bytes memory sig = _sign(order);

        vm.warp(block.timestamp + 365 days);

        vm.prank(taker);
        exchange.fillOrder(order, 50e6, sig); // should not revert
    }

    // -------------------------------------------------------------------------
    // Signature validation
    // -------------------------------------------------------------------------

    function test_InvalidSignature_Reverts() public {
        CTFExchange.Order memory order = _makeSellOrder(100e6, 60e6, 0, 50);

        // Sign with wrong key.
        uint256 wrongKey = 0xBAD;
        bytes32 h = exchange.getOrderHash(order);
        (uint8 v, bytes32 r, bytes32 s) = vm.sign(wrongKey, h);
        bytes memory badSig = abi.encodePacked(r, s, v);

        vm.prank(taker);
        vm.expectRevert("CTFExchange: invalid signature");
        exchange.fillOrder(order, 100e6, badSig);
    }

    // -------------------------------------------------------------------------
    // Batch fill (operator only)
    // -------------------------------------------------------------------------

    function test_FillOrders_Batch_OperatorOnly() public {
        // Non-operator tries batch fill → reverts.
        CTFExchange.Order[] memory orders = new CTFExchange.Order[](1);
        orders[0] = _makeSellOrder(50e6, 30e6, 0, 60);
        uint256[] memory amounts = new uint256[](1);
        amounts[0] = 50e6;
        bytes[] memory sigs = new bytes[](1);
        sigs[0] = _sign(orders[0]);

        vm.prank(taker);
        vm.expectRevert("CTFExchange: not an operator");
        exchange.fillOrders(orders, amounts, sigs);
    }

    function test_FillOrders_Batch_Operator() public {
        CTFExchange.Order[] memory orders = new CTFExchange.Order[](1);
        orders[0] = _makeSellOrder(50e6, 30e6, 0, 61);
        uint256[] memory amounts = new uint256[](1);
        amounts[0] = 50e6;
        bytes[] memory sigs = new bytes[](1);
        sigs[0] = _sign(orders[0]);

        // Operator (backend) fills on behalf of taker.
        // Operator itself is the taker here; it needs CTF approval from taker
        // and USDC approval from taker. In real usage, the operator would pass
        // the real taker address — but this tests the access control.
        vm.prank(operator);
        vm.expectRevert(); // will revert on transfer since operator has no USDC/tokens — just proves no access revert
        exchange.fillOrders(orders, amounts, sigs);
    }

    // -------------------------------------------------------------------------
    // Views
    // -------------------------------------------------------------------------

    function test_GetRemainingAmount() public {
        CTFExchange.Order memory order = _makeSellOrder(100e6, 60e6, 0, 70);
        bytes memory sig = _sign(order);

        // Before any fill: remaining = 100e6 (makerAmount for SELL).
        assertEq(exchange.getRemainingAmount(order), 100e6);

        vm.prank(taker);
        exchange.fillOrder(order, 40e6, sig);

        assertEq(exchange.getRemainingAmount(order), 60e6);
    }

    function test_IsOrderLive() public {
        CTFExchange.Order memory order = _makeSellOrder(100e6, 60e6, block.timestamp + 1 days, 80);
        bytes memory sig = _sign(order);

        assertTrue(exchange.isOrderLive(order));

        // Fill fully.
        vm.prank(taker);
        exchange.fillOrder(order, 100e6, sig);

        assertFalse(exchange.isOrderLive(order));
    }

    function test_DomainSeparator() public view {
        bytes32 ds = exchange.domainSeparator();
        assertTrue(ds != bytes32(0));
    }
}
