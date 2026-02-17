// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import "../src/RushTrade.sol";
import "@openzeppelin/contracts/token/ERC20/ERC20.sol";

/// @notice Mock USDC for testing
contract MockUSDC is ERC20 {
    constructor() ERC20("USD Coin", "USDC") {
        _mint(msg.sender, 1000000 * 10**6); // 1M USDC with 6 decimals
    }

    function decimals() public pure override returns (uint8) {
        return 6;
    }

    function mint(address to, uint256 amount) external {
        _mint(to, amount);
    }
}

contract DeployRushTrade is Script {
    function run() external {
        uint256 deployerPrivateKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        
        vm.startBroadcast(deployerPrivateKey);

        // Use existing USDC from Monad testnet deployment
        address usdcAddress = 0x534b2f3A21130d7a60830c2Df862319e593943A3;
        console.log("Using existing USDC at:", usdcAddress);

        // Deploy RushTrade
        console.log("\nDeploying RushTrade...");
        RushTrade rushTrade = new RushTrade(usdcAddress);
        console.log("RushTrade deployed at:", address(rushTrade));

        console.log("\n=== DEPLOYMENT SUMMARY ===");
        console.log("USDC:", usdcAddress);
        console.log("RushTrade:", address(rushTrade));
        console.log("Current Round ID:", rushTrade.currentRoundId());
        console.log("Fee Rate (bps):", rushTrade.feeRateBps());

        vm.stopBroadcast();
    }
}
