"""
test_time_sync.py
This module contains unit tests for the TimeNode class, verifying
clock synchronization accuracy and Lamport clock functionality.
"""

import unittest
import time
from time_sync import TimeNode

class TestTimeSync(unittest.TestCase):
    def test_clock_sync_accuracy(self):
        """Test if the clock offset is correctly applied via get_adjusted_time."""
        # Create a TimeNode for node1
        node = TimeNode("node1")
        
        # Apply a known clock skew
        skew_amount = 0.5
        node.simulate_clock_skew(skew_amount)
        
        # Capture the current time and get the adjusted time
        current_time = time.time()
        adjusted_time = node.get_adjusted_time()
        
        # Verify that the adjusted time is greater than the current time by approximately the skew amount
        # Tolerance of 0.1 seconds allowed
        self.assertAlmostEqual(adjusted_time, current_time + skew_amount, delta=0.1)

    def test_lamport_clock_ordering(self):
        """Test incrementing and updating the Lamport clock according to its rules."""
        # Create a TimeNode for node1
        node = TimeNode("node1")
        
        # Increment the Lamport clock three times
        node.increment_lamport()
        node.increment_lamport()
        current_val = node.increment_lamport()
        
        # Verify it is exactly 3
        self.assertEqual(current_val, 3)
        
        # Update with a received time of 10
        updated_val = node.update_lamport(10)
        
        # Verify it is now max(3, 10) + 1 = 11
        self.assertEqual(updated_val, 11)

    def test_sync_failure_fallback(self):
        """Test that the node falls back to its Lamport clock when sync fails."""
        # Create a TimeNode for node1
        node = TimeNode("node1")
        
        # Record Lamport clock before fallback
        initial_lamport = node.lamport_clock
        
        # Call sync_with_fallback with "node2" (which will fail)
        node.sync_with_fallback("node2")
        
        # Verify Lamport clock is incremented by exactly 1
        self.assertEqual(node.lamport_clock, initial_lamport + 1)

    def test_multiple_skews_accumulate(self):
        node = TimeNode("node1")
        node.simulate_clock_skew(0.3)
        node.simulate_clock_skew(0.2)
        self.assertAlmostEqual(node.clock_offset, 0.5, delta=0.001)

    def test_lamport_starts_at_zero(self):
        node = TimeNode("node1")
        self.assertEqual(node.lamport_clock, 0)

    def test_update_lamport_with_lower_value(self):
        node = TimeNode("node1")
        node.increment_lamport()  # clock = 1
        node.increment_lamport()  # clock = 2
        result = node.update_lamport(0)  # max(2, 0) + 1 = 3
        self.assertEqual(result, 3)

if __name__ == "__main__":
    unittest.main()
