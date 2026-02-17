# $rush

### Fast Markets for Fast Thinkers, who like to feel the Rush




To-Do
Home Page
Market Page
Gnosis CTF
Market Factory Contract
CTFExchange Contract
Backend - Database (Postgres), Redis, RabbitMQ, Celery


New API surface (/api/v1/terminal/clob/)

GET  /markets                          list markets
GET  /markets/{condition_id}           single market
GET  /markets/{condition_id}/orderbook live bids/asks
POST /markets/sync/{condition_id}      admin: sync from chain
POST /orders                           submit signed order
GET  /orders                           user's orders
DELETE /orders/{order_id}              cancel order
GET  /positions/{wallet}               on-chain CTF balances
GET  /fills                            user's fill history
GET  /eip712/{condition_id}            EIP-712 domain + types for frontend
GET  /health/chain                     Monad RPC health check