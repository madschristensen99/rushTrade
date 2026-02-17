// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Script.sol";

/// @notice Verify deployed contracts on Monadscan
/// @dev Run after deployment to verify contracts on the block explorer
///
/// Usage:
///   source .env && forge verify-contract \
///     --chain-id 10143 \
///     --num-of-optimizations 200 \
///     --watch \
///     --constructor-args $(cast abi-encode "constructor()") \
///     --etherscan-api-key $MONADSCAN_API_KEY \
///     --verifier-url https://explorer.testnet.monad.xyz/api \
///     <CONTRACT_ADDRESS> \
///     src/ConditionalTokens.sol:ConditionalTokens
///
/// This script provides the deployment addresses for easy copy-paste
contract Verify is Script {
    function run() external view {
        console2.log("\n=== Contract Verification Commands ===\n");
        
        console2.log("1. Verify ConditionalTokens:");
        console2.log("forge verify-contract \\");
        console2.log("  --chain-id 10143 \\");
        console2.log("  --num-of-optimizations 200 \\");
        console2.log("  --watch \\");
        console2.log("  --constructor-args $(cast abi-encode \"constructor()\") \\");
        console2.log("  --etherscan-api-key $MONADSCAN_API_KEY \\");
        console2.log("  --verifier-url https://explorer.testnet.monad.xyz/api \\");
        console2.log("  0x5Ec0724EA68a8F5c8aE7a87eaFe136730252f1fF \\");
        console2.log("  src/ConditionalTokens.sol:ConditionalTokens\n");
        
        console2.log("2. Verify MarketFactory:");
        console2.log("forge verify-contract \\");
        console2.log("  --chain-id 10143 \\");
        console2.log("  --num-of-optimizations 200 \\");
        console2.log("  --watch \\");
        console2.log("  --constructor-args $(cast abi-encode \"constructor(address,address)\" 0x5Ec0724EA68a8F5c8aE7a87eaFe136730252f1fF 0xDA932FF69169319CfC285c3BD42DC63B018994DF) \\");
        console2.log("  --etherscan-api-key $MONADSCAN_API_KEY \\");
        console2.log("  --verifier-url https://explorer.testnet.monad.xyz/api \\");
        console2.log("  0xba465E13D3d5Fb09627EBab1eA6e86293438c5E3 \\");
        console2.log("  src/MarketFactory.sol:MarketFactory\n");
        
        console2.log("3. Verify CTFExchange:");
        console2.log("forge verify-contract \\");
        console2.log("  --chain-id 10143 \\");
        console2.log("  --num-of-optimizations 200 \\");
        console2.log("  --watch \\");
        console2.log("  --constructor-args $(cast abi-encode \"constructor(address,address,address,address,uint256)\" 0x5Ec0724EA68a8F5c8aE7a87eaFe136730252f1fF 0x534b2f3A21130d7a60830c2Df862319e593943A3 0xDA932FF69169319CfC285c3BD42DC63B018994DF 0xDA932FF69169319CfC285c3BD42DC63B018994DF 50) \\");
        console2.log("  --etherscan-api-key $MONADSCAN_API_KEY \\");
        console2.log("  --verifier-url https://explorer.testnet.monad.xyz/api \\");
        console2.log("  0x5121fe4E7ba3130C56ea3e9E0C67C1b8EAcCCaA1 \\");
        console2.log("  src/CTFExchange.sol:CTFExchange\n");
    }
}
