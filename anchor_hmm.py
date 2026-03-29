"""
Anchor HMM -- Hidden Markov Model for focus state prediction
Hidden states: focused, depleted, stuck, bored, avoiding
Observations: relevant, drift, unsure, idle, break

Trains on past session data. Predicts when user will drift BEFORE it happens.
Needs 3+ sessions to train. Gets smarter every session.
"""

import numpy as np
import json
import os

STATES = ["focused", "depleted", "stuck", "bored", "avoiding"]
OBSERVATIONS = ["relevant", "drift", "unsure", "idle", "break"]


class AnchorHMM:
    def __init__(self, data_dir="."):
        self.model = None
        self.sequences = []
        self.sequences_path = os.path.join(data_dir, "anchor_hmm_sequences.json")
        self.model_path = os.path.join(data_dir, "anchor_hmm_model.pkl")
        self._load_sequences()

    def _load_sequences(self):
        if os.path.exists(self.sequences_path):
            try:
                with open(self.sequences_path, "r") as f:
                    self.sequences = json.load(f)
            except Exception:
                self.sequences = []

    def _save_sequences(self):
        try:
            with open(self.sequences_path, "w") as f:
                json.dump(self.sequences, f)
        except Exception:
            pass

    def _obs_to_int(self, obs: str) -> int:
        return OBSERVATIONS.index(obs) if obs in OBSERVATIONS else 0

    def save_session_sequence(self, observation_history: list):
        """Convert session observation history into a sequence and save."""
        sequence = []
        for obs in observation_history:
            summary = obs.get("summary", "").lower()
            if "relevant" in summary:
                sequence.append("relevant")
            elif "drift" in summary:
                sequence.append("drift")
            elif "unsure" in summary:
                sequence.append("unsure")
            elif "idle" in summary or "no keyboard" in summary:
                sequence.append("idle")
            elif "break" in summary:
                sequence.append("break")

        if len(sequence) >= 5:
            self.sequences.append(sequence)
            self._save_sequences()
            print(f"[HMM] Saved sequence ({len(sequence)} obs). Total sessions: {len(self.sequences)}")
            return True
        return False

    def train(self) -> bool:
        """Train HMM on collected sequences. Need at least 3 sessions."""
        if len(self.sequences) < 3:
            print(f"[HMM] Need 3+ sessions to train ({len(self.sequences)} so far)")
            return False

        try:
            from hmmlearn import hmm

            all_obs = []
            lengths = []
            for seq in self.sequences:
                int_seq = [self._obs_to_int(o) for o in seq]
                all_obs.extend(int_seq)
                lengths.append(len(int_seq))

            X = np.array(all_obs).reshape(-1, 1)

            self.model = hmm.CategoricalHMM(
                n_components=len(STATES),
                n_iter=100,
                random_state=42
            )
            self.model.fit(X, lengths)

            import joblib
            joblib.dump(self.model, self.model_path)

            print(f"[HMM] Trained on {len(self.sequences)} sessions")
            for i, state in enumerate(STATES):
                transitions = self.model.transmat_[i]
                top = sorted(zip(STATES, transitions), key=lambda x: -x[1])[:3]
                top_str = ", ".join(f"{s}: {p:.0%}" for s, p in top)
                print(f"  {state:10s} -> {top_str}")
            return True
        except Exception as e:
            print(f"[HMM] Train error: {repr(e)[:60]}")
            return False

    def predict_next_state(self, recent_observations: list) -> dict:
        """Predict the user's hidden state and what comes next."""
        if self.model is None:
            if os.path.exists(self.model_path):
                try:
                    import joblib
                    self.model = joblib.load(self.model_path)
                except Exception:
                    return None
            else:
                return None

        if len(recent_observations) < 3:
            return None

        try:
            int_obs = [self._obs_to_int(o) for o in recent_observations[-15:]]
            X = np.array(int_obs).reshape(-1, 1)

            _, state_sequence = self.model.decode(X, algorithm="viterbi")
            current_state = STATES[state_sequence[-1]]

            next_probs = self.model.transmat_[state_sequence[-1]]
            predictions = {STATES[i]: round(float(next_probs[i]), 2) for i in range(len(STATES))}
            predictions = dict(sorted(predictions.items(), key=lambda x: -x[1]))

            return {
                "current_state": current_state,
                "next_state_probs": predictions,
                "most_likely_next": max(predictions, key=predictions.get),
                "confidence": round(max(predictions.values()), 2)
            }
        except Exception as e:
            print(f"[HMM] Predict error: {repr(e)[:60]}")
            return None
