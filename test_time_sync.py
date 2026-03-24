import unittest
import time
from time_sync import TimeNode

class TestTimeSync(unittest.TestCase):
    def test_clock_sync_accuracy(self):
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

if __name__ == "__main__":
    unittest.main()
