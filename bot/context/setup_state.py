import logging

from bot.context.bot_state import BotState
from bot.constants import TimeUnit
from bot.configuration.resolvers import get_data_provider_configurations, load_data_provider

logger = logging.getLogger(__name__)


class SetupState(BotState):

    def __init__(self, context):
        logger.info("Initializing setup state ...")

        super(SetupState, self).__init__(context)

    def run(self):
        # Initialize all data provider executors
        self._initialize_data_providers()

        # from bot.context.data_providing_state import DataProvidingState
        # self.context.transition_to(DataProvidingState)
        # self.context.run()

    def _initialize_data_providers(self) -> None:
        """
        Function to load and initialize all the data providers.
        """

        print('initializing data providers')

    def stop(self):
        # Stopping all services
        pass

    def reconfigure(self):
        # Clean up and reconfigure all the services
        pass
