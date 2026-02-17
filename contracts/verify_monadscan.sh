#!/bin/bash

# Verify contracts on Monadscan by uploading to their web interface
# Since API verification isn't working, this generates the files needed for manual upload

set -e

source .env

echo "🔍 Generating verification files for Monadscan manual upload..."
echo ""

# Contract addresses
CTF_ADDRESS="0x5Ec0724EA68a8F5c8aE7a87eaFe136730252f1fF"
MARKET_FACTORY_ADDRESS="0xba465E13D3d5Fb09627EBab1eA6e86293438c5E3"
CTF_EXCHANGE_ADDRESS="0x5121fe4E7ba3130C56ea3e9E0C67C1b8EAcCCaA1"

mkdir -p verification_files

echo "📄 Flattening ConditionalTokens..."
forge flatten src/ConditionalTokens.sol > verification_files/ConditionalTokens_flat.sol

echo "📄 Flattening MarketFactory..."
forge flatten src/MarketFactory.sol > verification_files/MarketFactory_flat.sol

echo "📄 Flattening CTFExchange..."
forge flatten src/CTFExchange.sol > verification_files/CTFExchange_flat.sol

echo ""
echo "✅ Flattened contracts saved to ./verification_files/"
echo ""
echo "📋 Manual Verification Instructions:"
echo ""
echo "1. Go to Monadscan and find your contract:"
echo "   ConditionalTokens: https://testnet.monadscan.com/address/$CTF_ADDRESS#code"
echo "   MarketFactory:     https://testnet.monadscan.com/address/$MARKET_FACTORY_ADDRESS#code"
echo "   CTFExchange:       https://testnet.monadscan.com/address/$CTF_EXCHANGE_ADDRESS#code"
echo ""
echo "2. Click 'Verify and Publish'"
echo ""
echo "3. Fill in the form:"
echo "   - Compiler: v0.8.24"
echo "   - Optimization: Yes (200 runs)"
echo "   - License: MIT"
echo ""
echo "4. Upload the flattened source from ./verification_files/"
echo ""
echo "5. Constructor Arguments (if needed):"
echo "   ConditionalTokens: (none)"
echo "   MarketFactory:     0x0000000000000000000000005ec0724ea68a8f5c8ae7a87eafe136730252f1ff000000000000000000000000da932ff69169319cfc285c3bd42dc63b018994df"
echo "   CTFExchange:       0x0000000000000000000000005ec0724ea68a8f5c8ae7a87eafe136730252f1ff000000000000000000000000534b2f3a21130d7a60830c2df862319e593943a3000000000000000000000000da932ff69169319cfc285c3bd42dc63b018994df000000000000000000000000da932ff69169319cfc285c3bd42dc63b018994df0000000000000000000000000000000000000000000000000000000000000032"
