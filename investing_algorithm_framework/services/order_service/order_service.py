import logging
from datetime import datetime

from dateutil.tz import tzutc

from investing_algorithm_framework.domain import OrderType, OrderSide, \
    OperationalException, OrderStatus, MarketService, Order
from investing_algorithm_framework.services.repository_service \
    import RepositoryService

logger = logging.getLogger("investing_algorithm_framework")


class OrderService(RepositoryService):

    def __init__(
        self,
        configuration_service,
        order_repository,
        market_service: MarketService,
        position_repository,
        portfolio_repository,
        portfolio_configuration_service,
        portfolio_snapshot_service,
        market_credential_service,
        trade_service
    ):
        super(OrderService, self).__init__(order_repository)
        self.configuration_service = configuration_service
        self.order_repository = order_repository
        self.market_service: MarketService = market_service
        self.position_repository = position_repository
        self.portfolio_repository = portfolio_repository
        self.portfolio_configuration_service = portfolio_configuration_service
        self.portfolio_snapshot_service = portfolio_snapshot_service
        self.market_credential_service = market_credential_service
        self.trade_service = trade_service

    def create(self, data, execute=True, validate=True, sync=True) -> Order:
        portfolio_id = data["portfolio_id"]
        portfolio = self.portfolio_repository.get(portfolio_id)

        if validate:
            self.validate_order(data, portfolio)

        del data["portfolio_id"]
        symbol = data["target_symbol"]

        if validate:
            self.validate_order(data, portfolio)

        position = self._create_position_if_not_exists(symbol, portfolio)
        data["position_id"] = position.id
        data["remaining"] = data["amount"]
        data["status"] = OrderStatus.CREATED.value
        order = self.order_repository.create(data)
        order_id = order.id

        if sync:
            if OrderSide.BUY.equals(order.get_order_side()):
                self._sync_portfolio_with_created_buy_order(order)
            else:
                self._sync_portfolio_with_created_sell_order(order)

        self.create_snapshot(portfolio.id, created_at=order.created_at)

        if execute:
            portfolio.configuration = self.portfolio_configuration_service\
                .get(portfolio.identifier)
            self.execute_order(order_id, portfolio)

        # Create trade from buy order if buy order
        if OrderSide.BUY.equals(order.get_order_side()):
            self.trade_service.create_trade_from_buy_order(order)

        return order

    def update(self, object_id, data):
        previous_order = self.order_repository.get(object_id)
        trading_symbol_position = self.position_repository.find(
            {
                "id": previous_order.position_id,
                "symbol": previous_order.trading_symbol
            }
        )
        portfolio = self.portfolio_repository.get(
            trading_symbol_position.portfolio_id
        )
        new_order = self.order_repository.update(object_id, data)
        filled_difference = new_order.get_filled() \
            - previous_order.get_filled()

        if filled_difference:

            if OrderSide.BUY.equals(new_order.get_order_side()):
                self._sync_with_buy_order_filled(previous_order, new_order)
            else:
                self._sync_with_sell_order_filled(previous_order, new_order)

        if "status" in data:

            if OrderStatus.CANCELED.equals(new_order.get_status()):

                if OrderSide.BUY.equals(new_order.get_order_side()):
                    self._sync_with_buy_order_cancelled(new_order)
                else:
                    self._sync_with_sell_order_cancelled(new_order)

            if OrderStatus.EXPIRED.equals(new_order.get_status()):

                if OrderSide.BUY.equals(new_order.get_order_side()):
                    self._sync_with_buy_order_expired(new_order)
                else:
                    self._sync_with_sell_order_expired(new_order)

            if OrderStatus.REJECTED.equals(new_order.get_status()):

                if OrderSide.BUY.equals(new_order.get_order_side()):
                    self._sync_with_buy_order_rejected(new_order)
                else:
                    self._sync_with_sell_order_expired(new_order)

        if "updated_at" in data:
            created_at = data["updated_at"]
        else:
            created_at = datetime.now(tz=tzutc())

        self.create_snapshot(portfolio.id, created_at=created_at)
        return new_order

    def execute_order(self, order_id, portfolio):
        order = self.get(order_id)

        try:
            if OrderType.LIMIT.equals(order.get_order_type()):

                if OrderSide.BUY.equals(order.get_order_side()):
                    external_order = self.market_service\
                        .create_limit_buy_order(
                            target_symbol=order.get_target_symbol(),
                            trading_symbol=order.get_trading_symbol(),
                            amount=order.get_amount(),
                            price=order.get_price(),
                            market=portfolio.get_market()
                        )
                else:
                    external_order = self.market_service\
                        .create_limit_sell_order(
                            target_symbol=order.get_target_symbol(),
                            trading_symbol=order.get_trading_symbol(),
                            amount=order.get_amount(),
                            price=order.get_price(),
                            market=portfolio.get_market()
                        )
            else:
                if OrderSide.BUY.equals(order.get_order_side()):
                    raise OperationalException(
                        "Market buy order not supported"
                    )
                else:
                    external_order = self.market_service\
                        .create_market_sell_order(
                            target_symbol=order.get_target_symbol(),
                            trading_symbol=order.get_trading_symbol(),
                            amount=order.get_amount(),
                            market=portfolio.get_market()
                        )

            data = external_order.to_dict()
            data["status"] = OrderStatus.OPEN.value
            data["updated_at"] = datetime.now(tz=tzutc())
            return self.update(order_id, data)
        except Exception as e:
            logger.error("Error executing order: {}".format(e))
            return self.update(
                order_id,
                {
                    "status": OrderStatus.REJECTED.value,
                    "updated_at": datetime.now(tz=tzutc())
                }
            )

    def validate_order(self, order_data, portfolio):

        if OrderSide.BUY.equals(order_data["order_side"]):
            self.validate_buy_order(order_data, portfolio)
        else:
            self.validate_sell_order(order_data, portfolio)

        if OrderType.LIMIT.equals(order_data["order_type"]):
            self.validate_limit_order(order_data, portfolio)
        else:
            self.validate_market_order(order_data, portfolio)

    def validate_sell_order(self, order_data, portfolio):
        if not self.position_repository.exists(
            {
                "symbol": order_data["target_symbol"],
                "portfolio": portfolio.id
            }
        ):
            raise OperationalException(
                "Can't add sell order to non existing position"
            )

        position = self.position_repository\
            .find(
                {
                    "symbol": order_data["target_symbol"],
                    "portfolio": portfolio.id
                }
            )

        if position.get_amount() < order_data["amount"]:
            raise OperationalException(
                f"Order amount {order_data['amount']} is larger " +
                f"then amount of open position {position.get_amount()}"
            )

        if not order_data["trading_symbol"] == portfolio.trading_symbol:
            raise OperationalException(
                f"Can't add sell order with target "
                f"symbol {order_data['target_symbol']} to "
                f"portfolio with trading symbol {portfolio.trading_symbol}"
            )

    @staticmethod
    def validate_buy_order(order_data, portfolio):

        if not order_data["trading_symbol"] == portfolio.trading_symbol:
            raise OperationalException(
                f"Can't add buy order with trading "
                f"symbol {order_data['trading_symbol']} to "
                f"portfolio with trading symbol {portfolio.trading_symbol}"
            )

    def validate_limit_order(self, order_data, portfolio):

        if OrderSide.SELL.equals(order_data["order_side"]):
            amount = order_data["amount"]
            position = self.position_repository\
                .find(
                    {
                        "portfolio": portfolio.id,
                        "symbol": order_data["target_symbol"]
                    }
                )
            if amount > position.get_amount():
                raise OperationalException(
                    f"Order amount: {amount} {position.symbol}, is "
                    f"larger then position size: {position.get_amount()} "
                    f"{position.symbol} of the portfolio"
                )
        else:
            total_price = order_data["amount"] * order_data["price"]
            unallocated_position = self.position_repository\
                .find(
                    {
                        "portfolio": portfolio.id,
                        "symbol": portfolio.trading_symbol
                    }
                )
            unallocated_amount = unallocated_position.get_amount()

            if unallocated_amount is None:
                raise OperationalException(
                    "Unallocated amount of the portfolio is None" +
                    "can't validate limit order. Please check if " +
                    "the portfolio configuration is correct"
                )

            if unallocated_amount < total_price:
                raise OperationalException(
                    f"Order total: {total_price} "
                    f"{portfolio.trading_symbol}, is "
                    f"larger then unallocated size: {portfolio.unallocated} "
                    f"{portfolio.trading_symbol} of the portfolio"
                )

    def validate_market_order(self, order_data, portfolio):

        if OrderSide.BUY.equals(order_data["order_side"]):

            if "amount" not in order_data:
                raise OperationalException(
                    f"Market order needs an amount specified in the trading "
                    f"symbol {order_data['trading_symbol']}"
                )

            if order_data['amount'] > portfolio.unallocated:
                raise OperationalException(
                    f"Market order amount "
                    f"{order_data['amount']}"
                    f"{portfolio.trading_symbol.upper()} is larger then "
                    f"unallocated {portfolio.unallocated} "
                    f"{portfolio.trading_symbol.upper()}"
                )
        else:
            position = self.position_repository\
                .find(
                    {
                        "symbol": order_data["target_symbol"],
                        "portfolio": portfolio.id
                    }
                )

            if position is None:
                raise OperationalException(
                    "Can't add market sell order to non existing position"
                )

            if order_data['amount'] > position.get_amount():
                raise OperationalException(
                    "Sell order amount larger then position size"
                )

    def check_pending_orders(self, portfolio=None):
        """
        Function to check if
        """

        if portfolio is not None:
            pending_orders = self.get_all(
                {
                    "status": OrderStatus.OPEN.value,
                    "portfolio_id": portfolio.id
                }
            )
        else:
            pending_orders = self.get_all({"status": OrderStatus.OPEN.value})

        for order in pending_orders:
            position = self.position_repository.get(order.position_id)
            portfolio = self.portfolio_repository.get(position.portfolio_id)
            external_order = self.market_service\
                .get_order(order, market=portfolio.get_market())
            self.update(order.id, external_order.to_dict())

    def _create_position_if_not_exists(self, symbol, portfolio):
        if not self.position_repository.exists(
            {"portfolio": portfolio.id, "symbol": symbol}
        ):
            self.position_repository \
                .create({"portfolio_id": portfolio.id, "symbol": symbol})
            position = self.position_repository \
                .find({"portfolio": portfolio.id, "symbol": symbol})
        else:
            position = self.position_repository \
                .find({"portfolio": portfolio.id, "symbol": symbol})

        return position

    def _sync_portfolio_with_created_buy_order(self, order):
        position = self.position_repository.get(order.position_id)
        portfolio = self.portfolio_repository.get(position.portfolio_id)
        size = order.get_amount() * order.get_price()
        trading_symbol_position = self.position_repository.find(
            {
                "portfolio": portfolio.id,
                "symbol": portfolio.trading_symbol
            }
        )
        portfolio = self.portfolio_repository.update(
            portfolio.id, {"unallocated": portfolio.get_unallocated() - size}
        )
        position = self.position_repository.update(
            trading_symbol_position.id,
            {
                "amount": trading_symbol_position.get_amount() - size
            }
        )

    def _sync_portfolio_with_created_sell_order(self, order):
        """
        Function to sync the portfolio with a created sell order. The
        function will subtract the amount of the order from the position.
        The portfolio will not be updated because the sell order has not been
        filled.

        Args:
            order: Order object representing the sell order

        Returns:
            None
        """
        position = self.position_repository.get(order.position_id)
        self.position_repository.update(
            position.id,
            {
                "amount": position.get_amount() - order.get_amount()
            }
        )

    def cancel_order(self, order):
        self.check_pending_orders()
        order = self.order_repository.get(order.id)

        if order is not None:

            if OrderStatus.OPEN.equals(order.status):
                portfolio = self.portfolio_repository\
                    .find({"position": order.position_id})
                self.market_service.cancel_order(
                    order, market=portfolio.get_market()
                )

    def _sync_with_buy_order_filled(self, previous_order, current_order):
        filled_difference = current_order.get_filled() - \
                            previous_order.get_filled()
        filled_size = filled_difference * current_order.get_price()

        if filled_difference <= 0:
            return

        # Update position
        position = self.position_repository.get(current_order.position_id)

        self.position_repository.update(
            position.id,
            {
                "amount": position.get_amount() + filled_difference,
                "cost": position.get_cost() + filled_size,
            }
        )

        # Update portfolio
        portfolio = self.portfolio_repository.get(position.portfolio_id)
        self.portfolio_repository.update(
            portfolio.id,
            {
                "total_cost": portfolio.get_total_cost() + filled_size,
                "total_trade_volume": portfolio.get_total_trade_volume()
                + filled_size,
            }
        )

        self.trade_service.update_trade_with_buy_order(
            filled_difference, current_order
        )

    def _sync_with_sell_order_filled(self, previous_order, current_order):
        filled_difference = current_order.get_filled() - \
                            previous_order.get_filled()
        filled_size = filled_difference * current_order.get_price()

        if filled_difference <= 0:
            return

        # Get position
        position = self.position_repository.get(current_order.position_id)

        # Update the portfolio
        portfolio = self.portfolio_repository.get(position.portfolio_id)
        self.portfolio_repository.update(
            portfolio.id,
            {
                "unallocated": portfolio.get_unallocated() + filled_size,
                "total_trade_volume": portfolio.get_total_trade_volume()
                + filled_size,
            }
        )

        # Update the trading symbol position
        trading_symbol_position = self.position_repository.find(
            {
                "symbol": portfolio.trading_symbol,
                "portfolio": portfolio.id
            }
        )
        self.position_repository.update(
            trading_symbol_position.id,
            {
                "amount": trading_symbol_position.get_amount()
                + filled_size
            }
        )
        self.trade_service.update_trade_with_sell_order_filled(
            filled_difference, current_order
        )

    def _sync_with_buy_order_cancelled(self, order):
        remaining = order.get_amount() - order.get_filled()
        size = remaining * order.get_price()

        # Add the remaining amount to the portfolio
        portfolio = self.portfolio_repository.find(
            {
                "position": order.position_id
            }
        )
        self.portfolio_repository.update(
            portfolio.id,
            {
                "unallocated": portfolio.get_unallocated() + size
            }
        )

        # Add the remaining amount to the trading symbol position
        trading_symbol_position = self.position_repository.find(
            {
                "symbol": portfolio.trading_symbol,
                "portfolio": portfolio.id
            }
        )
        self.position_repository.update(
            trading_symbol_position.id,
            {
                "amount": trading_symbol_position.get_amount() + remaining
            }
        )

    def _sync_with_sell_order_cancelled(self, order):
        remaining = order.get_amount() - order.get_filled()

        # Add the remaining back to the position
        position = self.position_repository.get(order.position_id)
        self.position_repository.update(
            position.id,
            {
                "amount": position.get_amount() + remaining
            }
        )

    def _sync_with_buy_order_failed(self, order):
        remaining = order.get_amount() - order.get_filled()
        size = remaining * order.get_price()

        # Add the remaining amount to the portfolio
        portfolio = self.portfolio_repository.find(
            {
                "position": order.position_id
            }
        )
        self.portfolio_repository.update(
            portfolio.id,
            {
                "unallocated": portfolio.get_unallocated() + size
            }
        )

        # Add the remaining amount to the trading symbol position
        trading_symbol_position = self.position_repository.find(
            {
                "symbol": portfolio.trading_symbol,
                "portfolio": portfolio.id
            }
        )
        self.position_repository.update(
            trading_symbol_position.id,
            {
                "amount": trading_symbol_position.get_amount() + remaining
            }
        )

    def _sync_with_sell_order_failed(self, order):
        remaining = order.get_amount() - order.get_filled()

        # Add the remaining back to the position
        position = self.position_repository.get(order.position_id)
        self.position_repository.update(
            position.id,
            {
                "amount": position.get_amount() + remaining
            }
        )

    def _sync_with_buy_order_expired(self, order):
        remaining = order.get_amount() - order.get_filled()
        size = remaining * order.get_price()

        # Add the remaining amount to the portfolio
        portfolio = self.portfolio_repository.find(
            {
                "position": order.position_id
            }
        )
        self.portfolio_repository.update(
            portfolio.id,
            {
                "unallocated": portfolio.get_unallocated() + size
            }
        )

        # Add the remaining amount to the trading symbol position
        trading_symbol_position = self.position_repository.find(
            {
                "symbol": portfolio.trading_symbol,
                "portfolio": portfolio.id
            }
        )
        self.position_repository.update(
            trading_symbol_position.id,
            {
                "amount": trading_symbol_position.get_amount() + remaining
            }
        )

    def _sync_with_sell_order_expired(self, order):
        remaining = order.get_amount() - order.get_filled()

        # Add the remaining back to the position
        position = self.position_repository.get(order.position_id)
        self.position_repository.update(
            position.id,
            {
                "amount": position.get_amount() + remaining
            }
        )

    def _sync_with_buy_order_rejected(self, order):
        remaining = order.get_amount() - order.get_filled()
        size = remaining * order.get_price()

        # Add the remaining amount to the portfolio
        portfolio = self.portfolio_repository.find(
            {
                "position": order.position_id
            }
        )
        self.portfolio_repository.update(
            portfolio.id,
            {
                "unallocated": portfolio.get_unallocated() + size
            }
        )

        # Add the remaining amount to the trading symbol position
        trading_symbol_position = self.position_repository.find(
            {
                "symbol": portfolio.trading_symbol,
                "portfolio": portfolio.id
            }
        )
        self.position_repository.update(
            trading_symbol_position.id,
            {
                "amount": trading_symbol_position.get_amount() + remaining
            }
        )

    def _sync_with_sell_order_rejected(self, order):
        remaining = order.get_amount() - order.get_filled()

        # Add the remaining back to the position
        position = self.position_repository.get(order.position_id)
        self.position_repository.update(
            position.id,
            {
                "amount": position.get_amount() + remaining
            }
        )

    def create_snapshot(self, portfolio_id, created_at=None):

        if created_at is None:
            created_at = datetime.now(tz=tzutc())

        portfolio = self.portfolio_repository.get(portfolio_id)
        pending_orders = self.get_all(
            {
                "order_side": OrderSide.BUY.value,
                "status": OrderStatus.OPEN.value,
                "portfolio_id": portfolio.id
            }
        )
        created_orders = self.get_all(
            {
                "order_side": OrderSide.BUY.value,
                "status": OrderStatus.CREATED.value,
                "portfolio_id": portfolio.id
            }
        )
        return self.portfolio_snapshot_service.create_snapshot(
            portfolio,
            pending_orders=pending_orders,
            created_orders=created_orders,
            created_at=created_at
        )
