from tests.resources.test_base import TestBase


class TestAlgorithmContextInitialization(TestBase):

    def test(self):
        self.assertTrue(self.algo_app.algorithm._initializer.initialize_has_run)
