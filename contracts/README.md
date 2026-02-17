# RushTrade Contracts

Simplified on-chain prediction market for 60-second BTC price movements.

## Architecture

**Single Contract System** - No backend required, all logic on-chain.

### RushTrade.sol

A multi-token pool prediction market where users bet on which price range (box) BTC will land in after 60 seconds.

#### How It Works

1. **10 Boxes** - Each box represents a 1% price range:
   - Box 1: -5% to -4%
   - Box 2: -4% to -3%
   - Box 3: -3% to -2%
   - Box 4: -2% to -1%
   - Box 5: -1% to 0%
   - Box 6: 0% to +1%
   - Box 7: +1% to +2%
   - Box 8: +2% to +3%
   - Box 9: +3% to +4%
   - Box 10: +4% to +5%

2. **Liquidity Pools** - Each box has its own liquidity pool with a bonding curve (AMM)
   - Users buy shares in a box by depositing USDC
   - Share price increases as more people buy into that box
   - Users can sell shares back before the round ends

3. **Round Lifecycle**:
   - **Start**: Owner sets opening price
   - **Trading**: Users buy/sell shares in different boxes (60 seconds)
   - **Settlement**: Owner sets closing price, winning box is calculated
   - **Claim**: Winners claim their share of the losing pools

4. **Payouts**:
   - Winners get their original collateral back
   - Plus a proportional share of all losing boxes' collateral
   - Small fee (0.5%) taken from winnings

## Deployment

```bash
# Deploy to Monad Testnet
forge script script/DeployRushTrade.s.sol:DeployRushTrade \
  --rpc-url https://testnet-rpc.monad.xyz \
  --broadcast \
  --verify
```

## Key Functions

### User Functions

- `buyShares(uint8 boxId, uint256 collateralAmount)` - Buy shares in a box
- `sellShares(uint256 positionId, uint256 sharesToSell)` - Sell shares before round ends
- `claimWinnings(uint256 positionId)` - Claim winnings after round settles
- `claimMultipleWinnings(uint256[] positionIds)` - Batch claim multiple positions

### View Functions

- `getCurrentRound()` - Get current round info
- `getBoxPool(uint256 roundId, uint8 boxId)` - Get pool stats for a box
- `getUserPositions(address user)` - Get all user positions
- `calculatePotentialPayout(uint256 positionId)` - Calculate potential winnings

### Admin Functions (Owner Only)

- `setOpenPrice(int256 openPrice)` - Set opening price for current round
- `settleRound(int256 closePrice)` - End round and start new one
- `setFeeRate(uint256 newFeeRateBps)` - Update fee rate
- `withdrawTreasury(address to)` - Withdraw collected fees

## Example Flow

```solidity
// 1. User approves USDC
USDC.approve(address(rushTrade), 100e6);

// 2. User buys shares in Box 7 (betting on +1% to +2% price increase)
uint256 positionId = rushTrade.buyShares(7, 100e6);

// 3. Round ends, owner settles
rushTrade.settleRound(closePrice);

// 4. If Box 7 won, user claims winnings
rushTrade.claimWinnings(positionId);
```

## Frontend Integration

The frontend should:
1. Connect wallet (MetaMask)
2. Show current round info and countdown
3. Display all 10 boxes with current pool sizes
4. Allow users to buy shares in any box
5. Show user's positions and potential payouts
6. Allow claiming after round settles

## Network

- **Monad Testnet**
- Chain ID: 10143
- RPC: https://testnet-rpc.monad.xyz
- Explorer: https://testnet.monadscan.com

## Security

- ReentrancyGuard on all state-changing functions
- Owner controls for price oracle and round management
- SafeERC20 for token transfers
- Bonding curve prevents manipulation
