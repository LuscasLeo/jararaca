"""
Tests for message bus decorators.
"""

from jararaca.messagebus import MessageOf
from jararaca.messagebus.decorators import MessageBusController, MessageHandler
from jararaca.messagebus.message import Message


class SampleTestMessage(Message):
    """Sample message for testing message handlers."""

    MESSAGE_TOPIC = "test.topic"

    data: str


class TestMessageHandlerDecorator:
    """Test suite for @MessageHandler decorator."""

    def test_message_handler_basic(self) -> None:
        """Test basic @MessageHandler decorator."""

        @MessageBusController()
        class TestController:
            @MessageHandler(SampleTestMessage, auto_ack=True)
            async def handle_test(self, message: MessageOf[SampleTestMessage]) -> None:
                pass

        handler = MessageHandler.get_last(TestController.handle_test)
        assert handler is not None
        assert handler.message_type == SampleTestMessage
        assert handler.auto_ack is True

    def test_message_handler_with_timeout(self) -> None:
        """Test @MessageHandler with timeout."""

        @MessageBusController()
        class TestController:
            @MessageHandler(SampleTestMessage, timeout=30)
            async def handle_test(self, message: MessageOf[SampleTestMessage]) -> None:
                pass

        handler = MessageHandler.get_last(TestController.handle_test)
        assert handler is not None
        assert handler.timeout == 30

    def test_message_handler_with_nack_on_exception(self) -> None:
        """Test @MessageHandler with nack_on_exception."""

        @MessageBusController()
        class TestController:
            @MessageHandler(SampleTestMessage, nack_on_exception=True)
            async def handle_tests(self, message: MessageOf[SampleTestMessage]) -> None:
                pass

        handler = MessageHandler.get_last(TestController.handle_tests)
        assert handler is not None
        assert handler.requeue_on_exception is True

    def test_message_handler_with_custom_name(self) -> None:
        """Test @MessageHandler with custom name."""

        @MessageBusController()
        class TestController:
            @MessageHandler(SampleTestMessage, name="custom_handler")
            async def handle_test(self, message: MessageOf[SampleTestMessage]) -> None:
                pass

        handler = MessageHandler.get_last(TestController.handle_test)
        assert handler is not None
        assert handler.name == "custom_handler"

    def test_get_message_incoming_returns_none_for_undecorated(self) -> None:
        """Test that get_message_incoming returns None for undecorated functions."""

        async def not_a_handler(message: MessageOf[SampleTestMessage]) -> None:
            pass

        assert MessageHandler.get_last(not_a_handler) is None


class TestMessageBusControllerDecorator:
    """Test suite for @MessageBusController decorator."""

    def test_message_bus_controller_basic(self) -> None:
        """Test basic @MessageBusController decorator."""

        @MessageBusController()
        class TestController:
            @MessageHandler(SampleTestMessage)
            async def handle_test(self, message: MessageOf[SampleTestMessage]) -> None:
                pass

        assert hasattr(TestController, MessageBusController._ATTR_NAME)
