# RushTrade 🚀

### Fast Markets for Fast Thinkers, who like to feel the Rush

**60-second prediction markets** powered by real-time price data on Monad testnet.

---

## 🎯 What is RushTrade?

RushTrade is a high-speed prediction market platform where users can bet on BTC price movements in **60-second rounds**. Built on Monad testnet with real-time price feeds from Pyth Network.

### Key Features:
- ⚡ **60-second rounds** - Fast-paced prediction markets
- 📊 **Real-time BTC prices** - Powered by Pyth Network oracle
- 💰 **Multiple bets per round** - Place unlimited bets in each round
- 🎨 **Addictive UI** - Smooth animations and satisfying visual feedback
- 🔗 **On-chain settlement** - Gnosis Conditional Token Framework
- 💜 **Monad testnet** - Lightning-fast blockchain performance

---

## 📝 Deployed Contracts (Monad Testnet)

| Contract | Address | Explorer |
|----------|---------|----------|
| **ConditionalTokens** | `0x5ec0724ea68a8f5c8ae7a87eafe136730252f1ff` | [View](https://explorer.testnet.monad.xyz/address/0x5ec0724ea68a8f5c8ae7a87eafe136730252f1ff) |
| **MarketFactory** | `0xba465e13d3d5fb09627ebab1ea6e86293438c5e3` | [View](https://explorer.testnet.monad.xyz/address/0xba465e13d3d5fb09627ebab1ea6e86293438c5e3) |
| **CTFExchange** | `0x5121fe4e7ba3130c56ea3e9e0c67c1b8eacccaa1` | [View](https://explorer.testnet.monad.xyz/address/0x5121fe4e7ba3130c56ea3e9e0c67c1b8eacccaa1) |
| **USDC (Collateral)** | `0x534b2f3a21130d7a60830c2df862319e593943a3` | [View](https://explorer.testnet.monad.xyz/address/0x534b2f3a21130d7a60830c2df862319e593943a3) |

**Chain ID**: `10143` (Monad Testnet)  
**RPC URL**: `https://testnet-rpc.monad.xyz`

---

## 🏗️ Architecture

### Smart Contracts
- **ConditionalTokens.sol** - Gnosis CTF for YES/NO position tokens
- **MarketFactory.sol** - Creates and manages binary prediction markets
- **CTFExchange.sol** - Order book exchange for trading conditional tokens

### Frontend
- **Framework**: Vanilla JS with HTML5 Canvas
- **Wallet**: viem + MetaMask integration
- **Price Oracle**: Pyth Network (BTC/USD feed)
- **Animations**: 60fps requestAnimationFrame loop
- **UI**: Addictive brainrot-mode pulsing effects 🔥

### Backend (Planned)
- **Database**: PostgreSQL
- **Cache**: Redis
- **Queue**: RabbitMQ + Celery
- **API**: FastAPI/Express

---

## 🚀 Getting Started

### Frontend
```bash
cd frontend
python3 -m http.server 8000 --bind 127.0.0.1
# Visit http://localhost:8000
```

### Smart Contracts
```bash
cd contracts

# Install dependencies
forge install

# Run tests
forge test

# Deploy (requires .env setup)
forge script script/Deploy.s.sol \
  --rpc-url $MONAD_TESTNET_RPC_URL \
  --private-key $DEPLOYER_PRIVATE_KEY \
  --broadcast
```

---

## 📋 Roadmap

### ✅ Completed
- [x] Frontend UI with price chart and betting grid
- [x] Wallet authentication (MetaMask + Monad)
- [x] Real-time BTC prices from Pyth Network
- [x] Multiple bets per round system
- [x] Smooth 60fps animations
- [x] Smart contracts deployed to Monad testnet

### 🚧 In Progress
- [ ] Frontend integration with smart contracts
- [ ] Market creation via MarketFactory
- [ ] On-chain bet placement
- [ ] Oracle resolution system

### 📅 Planned
- [ ] Backend API for order book
- [ ] Off-chain order matching
- [ ] Trade history and analytics
- [ ] Leaderboard system
- [ ] Multi-asset support (ETH, SOL, etc.)

---

## 🛠️ Tech Stack

**Blockchain**
- Monad Testnet (EVM-compatible)
- Solidity ^0.8.24
- Foundry (Forge, Cast)
- OpenZeppelin Contracts

**Frontend**
- HTML5 Canvas
- Vanilla JavaScript (ES6+)
- viem (Ethereum library)
- Pyth Network SDK

**Smart Contract Framework**
- Gnosis Conditional Token Framework (CTF)
- EIP-712 typed signatures
- ERC-1155 position tokens

---

## 📄 API Endpoints (Planned)

### Markets
```
GET    /api/v1/markets                          # List all markets
GET    /api/v1/markets/{condition_id}           # Get market details
GET    /api/v1/markets/{condition_id}/orderbook # Live order book
POST   /api/v1/markets/sync/{condition_id}      # Sync from chain (admin)
```

### Orders
```
POST   /api/v1/orders                           # Submit signed order
GET    /api/v1/orders                           # User's orders
DELETE /api/v1/orders/{order_id}                # Cancel order
```

### Positions & Fills
```
GET    /api/v1/positions/{wallet}               # On-chain CTF balances
GET    /api/v1/fills                            # User's fill history
```

### Utilities
```
GET    /api/v1/eip712/{condition_id}            # EIP-712 signing data
GET    /api/v1/health/chain                     # Monad RPC health
```

---

## 🤝 Contributing

Contributions welcome! Feel free to open issues or submit PRs.

---

## 📜 License

MIT