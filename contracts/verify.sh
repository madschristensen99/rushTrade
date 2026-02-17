#!/bin/bash

# Verify deployed contracts on Monadscan programmatically
# Usage: ./verify.sh

set -e

source .env

echo "🔍 Verifying contracts on Monadscan programmatically..."
echo ""

# Contract addresses
CTF_ADDRESS="0x5Ec0724EA68a8F5c8aE7a87eaFe136730252f1fF"
MARKET_FACTORY_ADDRESS="0xba465E13D3d5Fb09627EBab1eA6e86293438c5E3"
CTF_EXCHANGE_ADDRESS="0x5121fe4E7ba3130C56ea3e9E0C67C1b8EAcCCaA1"
DEPLOYER_ADDRESS="0xDA932FF69169319CfC285c3BD42DC63B018994DF"

API_URL="https://api-testnet.monadscan.com/v2"

# Function to submit verification
verify_contract() {
    local address=$1
    local name=$2
    local source_file=$3
    local constructor_args=$4
    
    echo "Flattening $name..."
    forge flatten $source_file > /tmp/flat.sol
    
    echo "Submitting $name for verification..."
    
    response=$(curl -s -X POST "$API_URL" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        --data-urlencode "chainid=10143" \
        --data-urlencode "apikey=$MONADSCAN_API_KEY" \
        --data-urlencode "module=contract" \
        --data-urlencode "action=verifysourcecode" \
        --data-urlencode "contractaddress=$address" \
        --data-urlencode "sourceCode@/tmp/flat.sol" \
        --data-urlencode "codeformat=solidity-single-file" \
        --data-urlencode "contractname=$name" \
        --data-urlencode "compilerversion=v0.8.24+commit.e11b9ed9" \
        --data-urlencode "optimizationUsed=1" \
        --data-urlencode "runs=200" \
        --data-urlencode "constructorArguements=$constructor_args" \
        --data-urlencode "evmversion=cancun" \
        --data-urlencode "licenseType=3")
    
    echo "Response: $response"
    echo ""
    rm -f /tmp/flat.sol
}

echo "1️⃣  Verifying ConditionalTokens..."
verify_contract \
    "$CTF_ADDRESS" \
    "ConditionalTokens" \
    "src/ConditionalTokens.sol" \
    ""

echo "2️⃣  Verifying MarketFactory..."
verify_contract \
    "$MARKET_FACTORY_ADDRESS" \
    "MarketFactory" \
    "src/MarketFactory.sol" \
    "0000000000000000000000005ec0724ea68a8f5c8ae7a87eafe136730252f1ff000000000000000000000000da932ff69169319cfc285c3bd42dc63b018994df"

echo "3️⃣  Verifying CTFExchange..."
verify_contract \
    "$CTF_EXCHANGE_ADDRESS" \
    "CTFExchange" \
    "src/CTFExchange.sol" \
    "0000000000000000000000005ec0724ea68a8f5c8ae7a87eafe136730252f1ff000000000000000000000000534b2f3a21130d7a60830c2df862319e593943a3000000000000000000000000da932ff69169319cfc285c3bd42dc63b018994df000000000000000000000000da932ff69169319cfc285c3bd42dc63b018994df0000000000000000000000000000000000000000000000000000000000000032"

echo "✅ Verification requests submitted!"
echo ""
echo "View contracts on Monadscan:"
echo "https://testnet.monadscan.com/address/$CTF_ADDRESS#code"
echo "https://testnet.monadscan.com/address/$MARKET_FACTORY_ADDRESS#code"
echo "https://testnet.monadscan.com/address/$CTF_EXCHANGE_ADDRESS#code"
echo ""
echo "Note: Verification may take a few minutes to process."
