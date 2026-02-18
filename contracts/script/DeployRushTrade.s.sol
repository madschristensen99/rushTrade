// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import "../src/RushTrade.sol";

contract DeployRushTrade is Script {
    function run() external {
        uint256 deployerPrivateKey = vm.envUint("DEPLOYER_PRIVATE_KEY");

        vm.startBroadcast(deployerPrivateKey);

        // Contract addresses
        address usdcAddress = 0x534b2f3A21130d7a60830c2Df862319e593943A3;
        address pythAddress = 0x2880aB155794e7179c9eE2e38200202908C17B43; // Pyth on Monad testnet (from docs)
        bytes32 btcUsdPriceId = 0xe62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43; // BTC/USD
        
        console.log("Using USDC at:", usdcAddress);
        console.log("Using Pyth at:", pythAddress);

        RushTrade rushTrade = new RushTrade(usdcAddress, pythAddress, btcUsdPriceId);
        console.log("RushTrade deployed at:", address(rushTrade));
        console.log("\nNOTE: Call setOpenPriceFromOracle() or setOpenPrice() to start trading");

        vm.stopBroadcast();

        console.log("\n=== DEPLOYMENT SUMMARY ===");
        console.log("USDC:       ", usdcAddress);
        console.log("Pyth:       ", pythAddress);
        console.log("RushTrade:  ", address(rushTrade));
        console.log("Round ID:   ", rushTrade.currentRoundId());
    }
}
