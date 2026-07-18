"""
Mock-based unit tests for simulation/bridge.py MinecraftBridge.
Mocks mcrcon.MCRcon so no real server connection is needed.
"""

import pytest
from unittest.mock import patch, MagicMock


@patch("simulation.bridge.MCRcon")
class TestMinecraftBridge:
    """All tests in this class use a mocked MCRcon."""

    # ------------------------------------------------------------------ #
    # Connection lifecycle
    # ------------------------------------------------------------------ #

    def test_connect(self, mock_mcrcon):
        """connect() calls MCRcon.connect() and sets _connected."""
        from simulation.bridge import MinecraftBridge

        bridge = MinecraftBridge()
        assert bridge._connected is False
        mock_mcrcon.return_value.connect.assert_not_called()

        bridge.connect()

        mock_mcrcon.return_value.connect.assert_called_once()
        assert bridge._connected is True

    def test_disconnect(self, mock_mcrcon):
        """disconnect() calls MCRcon.disconnect() and clears _connected."""
        from simulation.bridge import MinecraftBridge

        bridge = MinecraftBridge()
        bridge.connect()
        assert bridge._connected is True

        bridge.disconnect()

        mock_mcrcon.return_value.disconnect.assert_called_once()
        assert bridge._connected is False

    def test_connect_already_connected(self, mock_mcrcon):
        """connect() is a no-op when already connected."""
        from simulation.bridge import MinecraftBridge

        bridge = MinecraftBridge()
        bridge._connected = True  # simulate already connected

        bridge.connect()

        mock_mcrcon.return_value.connect.assert_not_called()

    def test_disconnect_not_connected(self, mock_mcrcon):
        """disconnect() is a no-op when not connected."""
        from simulation.bridge import MinecraftBridge

        bridge = MinecraftBridge()
        bridge._connected = False

        bridge.disconnect()

        mock_mcrcon.return_value.disconnect.assert_not_called()

    # ------------------------------------------------------------------ #
    # Command execution
    # ------------------------------------------------------------------ #

    def test_run_command(self, mock_mcrcon):
        """run_command passes the command through and returns the response."""
        from simulation.bridge import MinecraftBridge

        bridge = MinecraftBridge()
        bridge.connect()
        mock_mcrcon.return_value.command.return_value = "response text"

        result = bridge.run_command("say hello")

        assert result == "response text"
        mock_mcrcon.return_value.command.assert_called_once_with("say hello")

    def test_run_command_not_connected(self, mock_mcrcon):
        """run_command auto-connects when not already connected."""
        from simulation.bridge import MinecraftBridge

        bridge = MinecraftBridge()
        # _connected is False by default — do NOT call bridge.connect()
        mock_mcrcon.return_value.command.return_value = "auto response"

        result = bridge.run_command("list")

        assert result == "auto response"
        # Should have connected itself
        mock_mcrcon.return_value.connect.assert_called_once()
        mock_mcrcon.return_value.command.assert_called_once_with("list")

    # ------------------------------------------------------------------ #
    # Block commands
    # ------------------------------------------------------------------ #

    def test_set_block_command(self, mock_mcrcon):
        """set_block sends the correct 'setblock x y z state' command."""
        from simulation.bridge import MinecraftBridge

        bridge = MinecraftBridge()
        bridge.connect()
        mock_mcrcon.return_value.command.return_value = ""

        bridge.set_block(10, 64, -3, "minecraft:stone")

        mock_mcrcon.return_value.command.assert_called_once_with(
            "setblock 10 64 -3 minecraft:stone"
        )

    def test_set_block_with_nbt(self, mock_mcrcon):
        """set_block appends NBT data to the block state."""
        from simulation.bridge import MinecraftBridge

        bridge = MinecraftBridge()
        bridge.connect()
        mock_mcrcon.return_value.command.return_value = ""

        bridge.set_block(1, 2, 3, "minecraft:chest[facing=north]", '{Items:[]}')

        mock_mcrcon.return_value.command.assert_called_once_with(
            'setblock 1 2 3 minecraft:chest[facing=north]{Items:[]}'
        )

    def test_fill_command(self, mock_mcrcon):
        """fill sends the correct 'fill ...' command."""
        from simulation.bridge import MinecraftBridge

        bridge = MinecraftBridge()
        bridge.connect()
        mock_mcrcon.return_value.command.return_value = ""

        bridge.fill(-5, 0, -5, 5, 10, 5, "minecraft:air")

        mock_mcrcon.return_value.command.assert_called_once_with(
            "fill -5 0 -5 5 10 5 minecraft:air"
        )

    def test_get_block_info(self, mock_mcrcon):
        """get_block_info sends the correct 'data get block ...' command."""
        from simulation.bridge import MinecraftBridge

        bridge = MinecraftBridge()
        bridge.connect()
        mock_mcrcon.return_value.command.return_value = "{Power: 15}"

        result = bridge.get_block_info(3, 4, 5)

        assert result == "{Power: 15}"
        mock_mcrcon.return_value.command.assert_called_once_with(
            "data get block 3 4 5"
        )

    # ------------------------------------------------------------------ #
    # Tick / freeze
    # ------------------------------------------------------------------ #

    def test_tick_warp(self, mock_mcrcon):
        """tick_warp sends the correct 'tick warp N' command."""
        from simulation.bridge import MinecraftBridge

        bridge = MinecraftBridge()
        bridge.connect()
        mock_mcrcon.return_value.command.return_value = ""

        bridge.tick_warp(100)

        mock_mcrcon.return_value.command.assert_called_once_with(
            "tick warp 100"
        )

    def test_freeze_time(self, mock_mcrcon):
        """freeze_time sends the 'tick freeze' command."""
        from simulation.bridge import MinecraftBridge

        bridge = MinecraftBridge()
        bridge.connect()
        mock_mcrcon.return_value.command.return_value = ""

        bridge.freeze_time()

        mock_mcrcon.return_value.command.assert_called_once_with("tick freeze")

    # ------------------------------------------------------------------ #
    # Timeout override
    # ------------------------------------------------------------------ #

    def test_timeout_override(self, mock_mcrcon):
        """When timeout differs from self.timeout, a separate connection is used."""
        from simulation.bridge import MinecraftBridge

        bridge = MinecraftBridge(timeout=120)
        mock_mcrcon.return_value.command.return_value = "quick response"

        result = bridge.run_command("entity summon", timeout=5)

        assert result == "quick response"
        # MCRcon was called with the override timeout
        mock_mcrcon.assert_called_with("localhost", "mira", 25575, timeout=5)
        # The temporary client was connected, used, and disconnected
        mock_mcrcon.return_value.connect.assert_called_once()
        mock_mcrcon.return_value.command.assert_called_once_with("entity summon")
        mock_mcrcon.return_value.disconnect.assert_called_once()

    # ------------------------------------------------------------------ #
    # Error handling
    # ------------------------------------------------------------------ #

    def test_error_handling(self, mock_mcrcon):
        """Exception from MCRcon is re-raised."""
        from simulation.bridge import MinecraftBridge

        bridge = MinecraftBridge()
        bridge.connect()
        mock_mcrcon.return_value.command.side_effect = Exception("RCON failure")

        with pytest.raises(Exception, match="RCON failure"):
            bridge.run_command("test")

    def test_error_sets_disconnected(self, mock_mcrcon):
        """On exception, _connected is set to False."""
        from simulation.bridge import MinecraftBridge

        bridge = MinecraftBridge()
        bridge.connect()
        assert bridge._connected is True

        mock_mcrcon.return_value.command.side_effect = Exception("RCON failure")

        with pytest.raises(Exception):
            bridge.run_command("test")

        assert bridge._connected is False
