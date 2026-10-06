import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from adaptive_brain import AdaptiveBrain

def test_adaptive_brain():
    print("Testing AdaptiveBrain ML Agent...")
    brain = AdaptiveBrain(learning_rate=0.1)
    
    # Simulate features
    features_win = {"cmo": 65.0, "vwap_dist": 0.5, "volume_delta": 1.2, "volatility": 0.02}
    features_loss = {"cmo": -40.0, "vwap_dist": -0.2, "volume_delta": 0.8, "volatility": 0.05}
    
    # Train the brain iteratively
    for _ in range(50):
        brain.learn_one(features_win, 1) # Positive outcome
        brain.learn_one(features_loss, 0) # Negative outcome
        
    # Test predictions
    prob_win = brain.predict_proba_one(features_win)
    prob_loss = brain.predict_proba_one(features_loss)
    
    print(f"Confidence for winning setup: {prob_win:.4f} (Expected > 0.5)")
    print(f"Confidence for losing setup: {prob_loss:.4f} (Expected < 0.5)")
    
    assert prob_win > 0.5, "Failed to learn winning patterns"
    assert prob_loss < 0.5, "Failed to learn losing patterns"
    
    # Test state persistence
    state = brain.get_state()
    assert state["count"] == 100, "State count mismatch"
    
    new_brain = AdaptiveBrain()
    new_brain.set_state(state)
    assert new_brain.predict_proba_one(features_win) == prob_win, "State serialization failed"
    
    try:
        print("✅ All AdaptiveBrain tests passed successfully!")
    except UnicodeEncodeError:
        print("[SUCCESS] All AdaptiveBrain tests passed successfully!")

if __name__ == "__main__":
    test_adaptive_brain()
