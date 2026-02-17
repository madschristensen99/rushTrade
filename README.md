# RushTrade

**60-Second Prediction Markets on Monad**

A fully on-chain prediction market where users bet on BTC price movements in 60-second rounds.

## 🎯 Overview

RushTrade is a simplified, high-speed prediction market built entirely on-chain. Users bet on which 1% price range BTC will land in after 60 seconds using a multi-token pool system.

### Key Features

- ⚡ **60-second rounds** - Fast-paced prediction markets
- 🎲 **10 outcome boxes** - Each representing a 1% price range (-5% to +5%)
- 💰 **Multi-token pools** - Each box has its own liquidity pool with bonding curve
- 🔗 **Fully on-chain** - No backend, no orderbook, all logic in smart contracts
- 🎨 **Beautiful UI** - Modern, responsive interface with real-time price feeds

## 🏗️ Architecture

### Single Contract System

**RushTrade.sol** - One contract handles everything:
- 10 separate liquidity pools (one per box)
- Bonding curve AMM for fair pricing
- Round management and settlement
- Winner payouts from losing pools

### How It Works

1. **Round Starts** (60 seconds)
   - Owner sets opening price from oracle
   - Users buy shares in boxes they think will win
   - Each box = 1% price range (e.g., Box 7 = +1% to +2%)

2. **Trading Phase**
   - Users buy/sell shares using USDC
   - Bonding curve: more buyers = higher share price
   - Can exit position anytime before round ends

3. **Settlement**
   - Owner sets closing price
   - Winning box calculated automatically
   - Winners claim proportional share of losing pools

4. **Payouts**
   - Winners get original collateral back
   - Plus share of all losing pools (minus 0.5% fee)
   - Example: If you own 10% of winning box shares, you get 10% of all losing pools

## 📦 Project Structure

```
rushTrade/
├── contracts/
│   ├── src/
│   │   └── RushTrade.sol          # Main contract
│   ├── script/
│   │   └── DeployRushTrade.s.sol  # Deployment script
│   └── README.md                   # Contract documentation
├── frontend/
│   └── index.html                  # Single-page app
└── README.md                       # This file
```

## 🚀 Deployed Contracts

### Monad Testnet

- **RushTrade**: `0xf20d297680cd451910eaa5fc58e73824d09e4688`
- **USDC (Collateral)**: `0x534b2f3A21130d7a60830c2Df862319e593943A3`

**Network Details:**
- Chain ID: `10143`
- RPC: `https://testnet-rpc.monad.xyz`
- Explorer: `https://testnet.monadscan.com`

**Deployment TX**: [0x303c2ba48883781c0e0e8a7cef06678d5722e897e3da8ea47eb25dc364b00049](https://testnet.monadscan.com/tx/0x303c2ba48883781c0e0e8a7cef06678d5722e897e3da8ea47eb25dc364b00049)

## 🎮 Usage

### For Users

1. **Connect Wallet**
   - Add Monad Testnet to MetaMask
   - Get testnet MON from faucet
   - Get testnet USDC

2. **Place Bets**
   - Choose a box (1-10) based on your price prediction
   - Approve USDC spending
   - Buy shares in your chosen box
   - Watch the 60-second countdown

3. **Claim Winnings**
   - After round settles, claim if you won
   - Receive original collateral + share of losing pools

### For Developers

```bash
# Clone repository
git clone https://github.com/madschristensen99/rushTrade.git
cd rushTrade

# Deploy contract
cd contracts
forge script script/DeployRushTrade.s.sol:DeployRushTrade \
  --rpc-url https://testnet-rpc.monad.xyz \
  --broadcast \
  --legacy

# Run frontend
cd ../frontend
# Open index.html in browser or serve with local server
```

## 📊 Box Ranges

| Box | Price Change | Example (from $100k) |
|-----|--------------|---------------------|
| 1   | -5% to -4%   | $95,000 - $96,000   |
| 2   | -4% to -3%   | $96,000 - $97,000   |
| 3   | -3% to -2%   | $97,000 - $98,000   |
| 4   | -2% to -1%   | $98,000 - $99,000   |
| 5   | -1% to 0%    | $99,000 - $100,000  |
| 6   | 0% to +1%    | $100,000 - $101,000 |
| 7   | +1% to +2%   | $101,000 - $102,000 |
| 8   | +2% to +3%   | $102,000 - $103,000 |
| 9   | +3% to +4%   | $103,000 - $104,000 |
| 10  | +4% to +5%   | $104,000 - $105,000 |

## 🛠️ Technical Stack

- **Smart Contracts**: Solidity 0.8.24, Foundry
- **Frontend**: Vanilla JS, Viem, Pyth Network (price feeds)
- **Network**: Monad Testnet
- **Collateral**: USDC (6 decimals)

## 🔐 Security

- ✅ ReentrancyGuard on all state-changing functions
- ✅ SafeERC20 for token transfers
- ✅ Owner-controlled price oracle (can be upgraded to Chainlink/Pyth)
- ✅ Bonding curve prevents pool manipulation
- ✅ No backend = no centralized attack surface

## 📈 Roadmap

- [ ] Integrate Chainlink/Pyth price oracle for automated rounds
- [ ] Add multiple assets (ETH, SOL, etc.)
- [ ] Implement automated round management
- [ ] Add liquidity provider rewards
- [ ] Deploy to Monad mainnet
- [ ] Mobile app

## 🤝 Contributing

Contributions welcome! Please open an issue or PR.

## 📄 License

MIT

## 🔗 Links

- **GitHub**: [madschristensen99/rushTrade](https://github.com/madschristensen99/rushTrade)
- **Explorer**: [MonadScan Testnet](https://testnet.monadscan.com)
- **Monad Docs**: [docs.monad.xyz](https://docs.monad.xyz)

---

Built with ⚡ on Monad
