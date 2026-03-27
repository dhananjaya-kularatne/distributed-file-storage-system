import unittest
import os
import json
import base64
from unittest.mock import patch, mock_open, MagicMock
from replication import Replicator

class TestReplication(unittest.TestCase):
    def setUp(self):
        # Initialize Replicator as node1
        self.replicator = Replicator("node1")

    @patch('replication.socket.socket')
    def test_replicate_to_followers_text(self, mock_socket):
        # Create a mock socket instance
        mock_sock_instance = MagicMock()
        mock_socket.return_value.__enter__.return_value = mock_sock_instance

        # Test with plain text
        self.replicator.replicate_to_followers("test.txt", "Hello World", 5)

        # Assert socket connect and sendall were called
        self.assertTrue(mock_sock_instance.connect.called)
        self.assertTrue(mock_sock_instance.sendall.called)

        # Extract the JSON that was sent across the mock socket
        call_args = mock_sock_instance.sendall.call_args[0][0]
        sent_message = json.loads(call_args.decode('utf-8'))

        self.assertEqual(sent_message["filename"], "test.txt")
        self.assertEqual(sent_message["data"], "Hello World")
        self.assertEqual(sent_message["is_binary"], False)
        self.assertEqual(sent_message["lamport_clock"], 5)

    @patch('replication.socket.socket')
    def test_replicate_to_followers_binary(self, mock_socket):
        mock_sock_instance = MagicMock()
        mock_socket.return_value.__enter__.return_value = mock_sock_instance

        # Test with binary bytes (like what you'd get from an image or PDF)
        binary_data = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR'
        self.replicator.replicate_to_followers("image.png", binary_data, 10)

        call_args = mock_sock_instance.sendall.call_args[0][0]
        sent_message = json.loads(call_args.decode('utf-8'))

        self.assertEqual(sent_message["filename"], "image.png")
        self.assertEqual(sent_message["is_binary"], True)
        self.assertEqual(sent_message["lamport_clock"], 10)
        
        # Ensure it was Base64 encoded inside the JSON
        expected_base64 = base64.b64encode(binary_data).decode('utf-8')
        self.assertEqual(sent_message["data"], expected_base64)

    @patch('builtins.open', new_callable=mock_open)
    def test_handle_replication_request_text(self, mock_op):
        message = {
            "type": "replicate_file",
            "filename": "incoming.txt",
            "data": "Replicated text",
            "is_binary": False,
            "lamport_clock": 15
        }
        self.replicator.handle_replication_request(message)

        # Ensure we tried to write to the basic file in 'w' (write text) mode
        expected_path = os.path.join(self.replicator.storage_path, "incoming.txt")
        mock_op.assert_any_call(expected_path, "w", encoding="utf-8")
        
    @patch('builtins.open', new_callable=mock_open)
    def test_handle_replication_request_binary(self, mock_op):
        binary_data = b'\x01\x02\x03\x04'
        encoded_data = base64.b64encode(binary_data).decode('utf-8')
        
        message = {
            "type": "replicate_file",
            "filename": "incoming.bin",
            "data": encoded_data,
            "is_binary": True,
            "lamport_clock": 20
        }
        self.replicator.handle_replication_request(message)

        # Ensure we tried to write to the file in 'wb' (write binary) mode without encoding
        expected_path = os.path.join(self.replicator.storage_path, "incoming.bin")
        mock_op.assert_any_call(expected_path, "wb", encoding=None)

    @patch('replication.socket.socket')
    def test_replicate_skips_on_connection_error(self, mock_socket):
        mock_sock_instance = MagicMock()
        mock_sock_instance.connect.side_effect = ConnectionRefusedError
        mock_socket.return_value.__enter__.return_value = mock_sock_instance

        try:
            self.replicator.replicate_to_followers("test.txt", "data", 1)
            print("PASSED - replication does not crash on connection error")
        except Exception as e:
            self.fail(f"FAILED - exception raised: {e}")

    def test_handle_missing_filename(self):
        message = {
            "type": "replicate_file",
            "data": "some data",
            "is_binary": False,
            "lamport_clock": 5
        }
        try:
            self.replicator.handle_replication_request(message)
            print("PASSED - missing filename handled gracefully")
        except Exception as e:
            self.fail(f"FAILED - crashed on missing filename: {e}")

if __name__ == '__main__':
    unittest.main()
