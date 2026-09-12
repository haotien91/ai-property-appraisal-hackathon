import unittest
from unittest.mock import Mock, patch
from client import ApiError, NetworkError, finish_import


class ClientRecoveryTests(unittest.TestCase):
    @patch('client.time.sleep')
    def test_network_timeout_polls_existing_run(self, sleep):
        client = Mock()
        client.call.side_effect = [NetworkError('timeout'), {'status': 'imported', 'run_id': 'same'}]
        self.assertEqual(finish_import(client, 'same')['run_id'], 'same')
        self.assertEqual(client.call.call_args.args, ('GET', '/v1/imports/same'))

    def test_validation_error_is_not_hidden_by_poll(self):
        client = Mock()
        client.call.side_effect = ApiError(422, {'error': {'code': 'INVALID_BUNDLE'}})
        with self.assertRaises(ApiError): finish_import(client, 'same')
        self.assertEqual(client.call.call_count, 1)

    @patch('client.time.sleep')
    def test_existing_processing_run_is_polled(self, sleep):
        client = Mock()
        client.call.side_effect = [ApiError(409, {'error': {'code': 'IMPORT_IN_PROGRESS'}}),
                                   {'status': 'processing'}, {'status': 'imported', 'run_id': 'same'}]
        self.assertEqual(finish_import(client, 'same')['run_id'], 'same')
        self.assertEqual(sum(c.args[0] == 'POST' for c in client.call.call_args_list), 1)
