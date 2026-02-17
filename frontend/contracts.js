// RushTrade Contract Configuration
// Monad Testnet Deployment - February 2026

export const CONTRACTS = {
  ConditionalTokens: {
    address: '0x5ec0724ea68a8f5c8ae7a87eafe136730252f1ff',
    explorer: 'https://testnet.monadscan.com/address/0x5ec0724ea68a8f5c8ae7a87eafe136730252f1ff'
  },
  MarketFactory: {
    address: '0xba465e13d3d5fb09627ebab1ea6e86293438c5e3',
    explorer: 'https://testnet.monadscan.com/address/0xba465e13d3d5fb09627ebab1ea6e86293438c5e3'
  },
  CTFExchange: {
    address: '0x5121fe4e7ba3130c56ea3e9e0c67c1b8eacccaa1',
    explorer: 'https://testnet.monadscan.com/address/0x5121fe4e7ba3130c56ea3e9e0c67c1b8eacccaa1'
  },
  USDC: {
    address: '0x534b2f3a21130d7a60830c2df862319e593943a3',
    explorer: 'https://testnet.monadscan.com/address/0x534b2f3a21130d7a60830c2df862319e593943a3'
  }
};

export const CHAIN_CONFIG = {
  chainId: 10143,
  name: 'Monad Testnet',
  rpcUrl: 'https://testnet-rpc.monad.xyz',
  explorer: 'https://testnet.monadscan.com',
  nativeCurrency: {
    name: 'Monad',
    symbol: 'MON',
    decimals: 18
  }
};

// Load ABIs
export async function loadABI(contractName) {
  try {
    const response = await fetch(`./abis/${contractName}.json`);
    return await response.json();
  } catch (error) {
    console.error(`Failed to load ABI for ${contractName}:`, error);
    return null;
  }
}
