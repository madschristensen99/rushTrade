// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import "../src/RushTrade.sol";

contract DeployRushTrade is Script {
    function run() external {
        uint256 deployerPrivateKey = vm.envUint("DEPLOYER_PRIVATE_KEY");

        vm.startBroadcast(deployerPrivateKey);

        address usdcAddress = 0x534b2f3A21130d7a60830c2Df862319e593943A3;
        console.log("Using USDC at:", usdcAddress);

        RushTrade rushTrade = new RushTrade(usdcAddress);
        console.log("RushTrade deployed at:", address(rushTrade));

        // Set the opening price immediately so buyShares works from block 0.
        // Using Pyth-compatible 8-decimal integer: $97,000 = 9700000000000
        // Update this to today's BTC price before deploying.
        int256 openPrice = 9700000000000; // $97,000 with 8 decimal places
        rushTrade.setOpenPrice(openPrice);
        console.log("Open price set:", uint256(openPrice));

        vm.stopBroadcast();

        console.log("\n=== DEPLOYMENT SUMMARY ===");
        console.log("USDC:      ", usdcAddress);
        console.log("RushTrade: ", address(rushTrade));
        console.log("Round ID:  ", rushTrade.currentRoundId());
    }
}
