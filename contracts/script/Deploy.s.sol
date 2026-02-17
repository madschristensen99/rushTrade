// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";
import "../src/ConditionalTokens.sol";
import "../src/MarketFactory.sol";
import "../src/CTFExchange.sol";

/// @notice Deploys ConditionalTokens, MarketFactory, and CTFExchange to Monad.
///
/// Usage:
///   forge script script/Deploy.s.sol \
///     --rpc-url $MONAD_TESTNET_RPC_URL \
///     --private-key $DEPLOYER_PRIVATE_KEY \
///     --broadcast \
///     --verify
///
/// Required environment variables:
///   DEPLOYER_PRIVATE_KEY   – deployer EOA private key
///   COLLATERAL_TOKEN       – address of the whitelisted collateral (e.g. USDC on Monad testnet)
///   FEE_RECIPIENT          – address that collects protocol fees
///   PROTOCOL_FEE_BPS       – protocol fee in basis points (e.g. 50 = 0.5%)
///
/// Optional (to skip redeploying an already-deployed CTF):
///   EXISTING_CTF           – if set, skips deploying ConditionalTokens and uses this address
contract Deploy is Script {
    function run() external {
        uint256 deployerKey = vm.envUint("DEPLOYER_PRIVATE_KEY");
        address deployer = vm.addr(deployerKey);

        address collateralToken = vm.envAddress("COLLATERAL_TOKEN");
        address feeRecipient = vm.envAddress("FEE_RECIPIENT");
        uint256 protocolFeeBps = vm.envUint("PROTOCOL_FEE_BPS");

        vm.startBroadcast(deployerKey);

        // 1. Deploy (or reuse) ConditionalTokens.
        address ctfAddress = vm.envOr("EXISTING_CTF", address(0));
        ConditionalTokens ctf;
        if (ctfAddress == address(0)) {
            ctf = new ConditionalTokens();
            console2.log("ConditionalTokens deployed at:", address(ctf));
        } else {
            ctf = ConditionalTokens(ctfAddress);
            console2.log("Using existing ConditionalTokens at:", address(ctf));
        }

        // 2. Deploy MarketFactory.
        MarketFactory factory = new MarketFactory(address(ctf), deployer);
        console2.log("MarketFactory deployed at:", address(factory));

        // 3. Whitelist the collateral token.
        factory.setCollateralApproval(collateralToken, true);
        console2.log("Collateral approved:", collateralToken);

        // 4. Deploy CTFExchange.
        CTFExchange exchange = new CTFExchange(
            address(ctf),
            collateralToken,
            deployer,
            feeRecipient,
            protocolFeeBps
        );
        console2.log("CTFExchange deployed at:", address(exchange));
        console2.log("Protocol fee:", protocolFeeBps, "bps");

        vm.stopBroadcast();

        // Print a deployment summary for easy copy-paste into the backend .env.
        console2.log("\n=== Deployment Summary ===");
        console2.log("CTF_ADDRESS=", address(ctf));
        console2.log("MARKET_FACTORY_ADDRESS=", address(factory));
        console2.log("CTF_EXCHANGE_ADDRESS=", address(exchange));
        console2.log("COLLATERAL_TOKEN=", collateralToken);
        console2.log("FEE_RECIPIENT=", feeRecipient);
        console2.log("DEPLOYER=", deployer);
    }
}
