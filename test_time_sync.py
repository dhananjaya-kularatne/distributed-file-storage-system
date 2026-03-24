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

if __name__ == "__main__":
    unittest.main()
