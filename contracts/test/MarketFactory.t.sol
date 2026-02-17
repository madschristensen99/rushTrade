// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";
import "../src/ConditionalTokens.sol";
import "../src/MarketFactory.sol";

/// @dev Minimal ERC-20 mock for collateral.
contract MockERC20 {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    string public name = "Mock USDC";
    string public symbol = "mUSDC";
    uint8 public decimals = 6;

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}

contract MarketFactoryTest is Test {
    ConditionalTokens public ctf;
    MarketFactory public factory;
    MockERC20 public usdc;

    address public owner = makeAddr("owner");
    address public oracle = makeAddr("oracle");
    address public alice = makeAddr("alice");

    bytes32 public questionId = keccak256("Will ETH hit $10k by end of 2026?");

    function setUp() public {
        usdc = new MockERC20();

        vm.startPrank(owner);
        ctf = new ConditionalTokens();
        factory = new MarketFactory(address(ctf), owner);
        factory.setCollateralApproval(address(usdc), true);
        vm.stopPrank();
    }

    // -------------------------------------------------------------------------
    // Collateral approval
    // -------------------------------------------------------------------------

    function test_SetCollateralApproval() public {
        address token = makeAddr("token");
        vm.prank(owner);
        factory.setCollateralApproval(token, true);
        assertTrue(factory.approvedCollateral(token));

        vm.prank(owner);
        factory.setCollateralApproval(token, false);
        assertFalse(factory.approvedCollateral(token));
    }

    function test_SetCollateralApproval_RevertNonOwner() public {
        vm.prank(alice);
        vm.expectRevert();
        factory.setCollateralApproval(address(usdc), false);
    }

    function test_SetCollateralApproval_RevertZeroAddress() public {
        vm.prank(owner);
        vm.expectRevert("MarketFactory: zero token address");
        factory.setCollateralApproval(address(0), true);
    }

    // -------------------------------------------------------------------------
    // Market creation
    // -------------------------------------------------------------------------

    function test_CreateMarket() public {
        uint256 resolutionTime = block.timestamp + 7 days;

        vm.prank(owner);
        bytes32 conditionId = factory.createMarket(
            questionId,
            oracle,
            address(usdc),
            resolutionTime,
            "Will ETH hit $10k?",
            "Resolves YES if ETH closes >= $10,000 on any major exchange before the deadline.",
            "Crypto"
        );

        assertEq(factory.totalMarkets(), 1);

        MarketFactory.Market memory m = factory.getMarket(conditionId);
        assertEq(m.questionId, questionId);
        assertEq(m.oracle, oracle);
        assertEq(m.collateralToken, address(usdc));
        assertEq(m.resolutionTime, resolutionTime);
        assertFalse(m.resolved);
        assertEq(m.title, "Will ETH hit $10k?");
        assertEq(m.category, "Crypto");

        // conditionId should match what CTF computes.
        bytes32 expectedConditionId = ctf.getConditionId(oracle, questionId, 2);
        assertEq(conditionId, expectedConditionId);
    }

    function test_CreateMarket_EmitsEvent() public {
        uint256 resolutionTime = block.timestamp + 7 days;
        bytes32 expectedConditionId = ctf.getConditionId(oracle, questionId, 2);

        vm.expectEmit(true, true, true, true);
        emit MarketFactory.MarketCreated(
            expectedConditionId,
            questionId,
            oracle,
            address(usdc),
            resolutionTime,
            "Test Market",
            "Sports"
        );

        vm.prank(owner);
        factory.createMarket(questionId, oracle, address(usdc), resolutionTime, "Test Market", "Desc", "Sports");
    }

    function test_CreateMarket_RevertNonOwner() public {
        vm.prank(alice);
        vm.expectRevert();
        factory.createMarket(
            questionId, oracle, address(usdc), block.timestamp + 1 days, "Title", "Desc", "Cat"
        );
    }

    function test_CreateMarket_RevertUnapprovedCollateral() public {
        address badToken = makeAddr("badToken");
        vm.prank(owner);
        vm.expectRevert("MarketFactory: collateral not approved");
        factory.createMarket(
            questionId, oracle, badToken, block.timestamp + 1 days, "Title", "Desc", "Cat"
        );
    }

    function test_CreateMarket_RevertZeroOracle() public {
        vm.prank(owner);
        vm.expectRevert("MarketFactory: zero oracle address");
        factory.createMarket(
            questionId, address(0), address(usdc), block.timestamp + 1 days, "Title", "Desc", "Cat"
        );
    }

    function test_CreateMarket_RevertPastResolutionTime() public {
        vm.prank(owner);
        vm.expectRevert("MarketFactory: resolution time in the past");
        factory.createMarket(
            questionId, oracle, address(usdc), block.timestamp - 1, "Title", "Desc", "Cat"
        );
    }

    function test_CreateMarket_RevertEmptyTitle() public {
        vm.prank(owner);
        vm.expectRevert("MarketFactory: empty title");
        factory.createMarket(
            questionId, oracle, address(usdc), block.timestamp + 1 days, "", "Desc", "Cat"
        );
    }

    function test_CreateDuplicateMarket_Reverts() public {
        vm.startPrank(owner);
        factory.createMarket(
            questionId, oracle, address(usdc), block.timestamp + 1 days, "Market 1", "Desc", "Cat"
        );
        vm.expectRevert("MarketFactory: market already exists");
        factory.createMarket(
            questionId, oracle, address(usdc), block.timestamp + 2 days, "Market 2", "Desc", "Cat"
        );
        vm.stopPrank();
    }

    // -------------------------------------------------------------------------
    // Resolution
    // -------------------------------------------------------------------------

    function _createTestMarket() internal returns (bytes32 conditionId) {
        vm.prank(owner);
        conditionId = factory.createMarket(
            questionId,
            oracle,
            address(usdc),
            block.timestamp + 7 days,
            "Test Market",
            "Description",
            "Crypto"
        );
    }

    function test_ResolveMarket_YesWin() public {
        bytes32 conditionId = _createTestMarket();

        // Warp past resolution time.
        vm.warp(block.timestamp + 8 days);

        uint256[] memory payouts = new uint256[](2);
        payouts[0] = 1; // YES wins
        payouts[1] = 0;

        vm.prank(oracle);
        factory.resolveMarket(conditionId, payouts);

        MarketFactory.Market memory m = factory.getMarket(conditionId);
        assertTrue(m.resolved);
        assertTrue(ctf.isResolved(conditionId));
    }

    function test_ResolveMarket_NoWin() public {
        bytes32 conditionId = _createTestMarket();
        vm.warp(block.timestamp + 8 days);

        uint256[] memory payouts = new uint256[](2);
        payouts[0] = 0;
        payouts[1] = 1; // NO wins

        vm.prank(oracle);
        factory.resolveMarket(conditionId, payouts);

        assertTrue(ctf.isResolved(conditionId));
        assertEq(ctf.payoutDenominator(conditionId), 1);
    }

    function test_ResolveMarket_Invalid_SplitPayout() public {
        bytes32 conditionId = _createTestMarket();
        vm.warp(block.timestamp + 8 days);

        uint256[] memory payouts = new uint256[](2);
        payouts[0] = 1;
        payouts[1] = 1; // invalid / 50-50 split

        vm.prank(oracle);
        factory.resolveMarket(conditionId, payouts);

        assertTrue(ctf.isResolved(conditionId));
        assertEq(ctf.payoutDenominator(conditionId), 2);
    }

    function test_ResolveMarket_RevertNonOracle() public {
        bytes32 conditionId = _createTestMarket();
        vm.warp(block.timestamp + 8 days);

        uint256[] memory payouts = new uint256[](2);
        payouts[0] = 1;
        payouts[1] = 0;

        vm.prank(alice);
        vm.expectRevert("MarketFactory: caller is not the oracle");
        factory.resolveMarket(conditionId, payouts);
    }

    function test_ResolveMarket_RevertTooEarly() public {
        bytes32 conditionId = _createTestMarket();
        // Don't warp — resolution time is 7 days in the future.

        uint256[] memory payouts = new uint256[](2);
        payouts[0] = 1;
        payouts[1] = 0;

        vm.prank(oracle);
        vm.expectRevert("MarketFactory: too early to resolve");
        factory.resolveMarket(conditionId, payouts);
    }

    function test_ResolveMarket_RevertDoubleResolution() public {
        bytes32 conditionId = _createTestMarket();
        vm.warp(block.timestamp + 8 days);

        uint256[] memory payouts = new uint256[](2);
        payouts[0] = 1;
        payouts[1] = 0;

        vm.prank(oracle);
        factory.resolveMarket(conditionId, payouts);

        vm.prank(oracle);
        vm.expectRevert("MarketFactory: already resolved");
        factory.resolveMarket(conditionId, payouts);
    }

    function test_ResolveMarket_RevertNonexistentMarket() public {
        uint256[] memory payouts = new uint256[](2);
        payouts[0] = 1;
        payouts[1] = 0;

        vm.prank(oracle);
        vm.expectRevert("MarketFactory: market does not exist");
        factory.resolveMarket(bytes32("nonexistent"), payouts);
    }

    // -------------------------------------------------------------------------
    // Pagination
    // -------------------------------------------------------------------------

    function test_GetMarkets_Pagination() public {
        uint256 resolutionTime = block.timestamp + 1 days;
        vm.startPrank(owner);
        for (uint256 i = 0; i < 5; i++) {
            bytes32 qId = keccak256(abi.encode("question", i));
            address orc = makeAddr(string(abi.encode("oracle", i)));
            factory.createMarket(qId, orc, address(usdc), resolutionTime, "T", "D", "C");
        }
        vm.stopPrank();

        assertEq(factory.totalMarkets(), 5);

        bytes32[] memory page = factory.getMarkets(0, 3);
        assertEq(page.length, 3);

        bytes32[] memory rest = factory.getMarkets(3, 10);
        assertEq(rest.length, 2);

        bytes32[] memory empty = factory.getMarkets(10, 5);
        assertEq(empty.length, 0);
    }

    // -------------------------------------------------------------------------
    // Position IDs
    // -------------------------------------------------------------------------

    function test_GetPositionIds() public {
        bytes32 conditionId = _createTestMarket();
        (uint256 yesId, uint256 noId) = factory.getPositionIds(conditionId);

        // Must match what CTF computes directly.
        (uint256 expectedYes, uint256 expectedNo) = ctf.getPositionIds(IERC20(address(usdc)), conditionId);
        assertEq(yesId, expectedYes);
        assertEq(noId, expectedNo);
        assertTrue(yesId != noId);
    }

    // -------------------------------------------------------------------------
    // Full round-trip: create → split → resolve → redeem
    // -------------------------------------------------------------------------

    function test_FullRoundTrip_YesWin() public {
        bytes32 conditionId = _createTestMarket();
        (uint256 yesId, uint256 noId) = factory.getPositionIds(conditionId);

        uint256 amount = 100e6; // 100 USDC
        usdc.mint(alice, amount);

        // Alice splits 100 USDC into 100 YES + 100 NO tokens.
        vm.startPrank(alice);
        IERC20(address(usdc)).approve(address(ctf), amount);
        uint256[] memory partition = new uint256[](2);
        partition[0] = 1; // YES
        partition[1] = 2; // NO
        ctf.splitPosition(IERC20(address(usdc)), bytes32(0), conditionId, partition, amount);
        vm.stopPrank();

        assertEq(ctf.balanceOf(alice, yesId), amount);
        assertEq(ctf.balanceOf(alice, noId), amount);

        // Resolve: YES wins.
        vm.warp(block.timestamp + 8 days);
        uint256[] memory payouts = new uint256[](2);
        payouts[0] = 1;
        payouts[1] = 0;
        vm.prank(oracle);
        factory.resolveMarket(conditionId, payouts);

        // Alice redeems her YES tokens → gets back 100 USDC.
        uint256[] memory indexSets = new uint256[](1);
        indexSets[0] = 1; // YES position

        uint256 usdcBefore = usdc.balanceOf(alice);
        vm.prank(alice);
        ctf.redeemPositions(IERC20(address(usdc)), bytes32(0), conditionId, indexSets);

        assertEq(usdc.balanceOf(alice), usdcBefore + amount);
        assertEq(ctf.balanceOf(alice, yesId), 0);

        // NO tokens are now worthless; redeeming them yields 0.
        indexSets[0] = 2;
        uint256 usdcBeforeNo = usdc.balanceOf(alice);
        vm.prank(alice);
        ctf.redeemPositions(IERC20(address(usdc)), bytes32(0), conditionId, indexSets);
        assertEq(usdc.balanceOf(alice), usdcBeforeNo); // unchanged
    }
}
