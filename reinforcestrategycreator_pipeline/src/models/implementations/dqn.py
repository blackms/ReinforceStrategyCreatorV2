"""Deep Q-Network (DQN) implementation."""

import numpy as np
from typing import Any, Dict, List, Optional, Tuple
import json
from collections import deque
import random

from ..base import ModelBase
# Removed module-level import to break circular dependency
# MetricsCalculator will be imported lazily in __init__ method


class ReplayBuffer:
    """Experience replay buffer for DQN."""
    
    def __init__(self, capacity: int):
        """Initialize replay buffer.
        
        Args:
            capacity: Maximum number of experiences to store
        """
        self.buffer = deque(maxlen=capacity)
    
    def push(self, state: np.ndarray, action: int, reward: float, 
             next_state: np.ndarray, done: bool) -> None:
        """Add an experience to the buffer.
        
        Args:
            state: Current state
            action: Action taken
            reward: Reward received
            next_state: Next state
            done: Whether episode ended
        """
        self.buffer.append((state, action, reward, next_state, done))
    
    def sample(self, batch_size: int) -> Tuple[np.ndarray, ...]:
        """Sample a batch of experiences.
        
        Args:
            batch_size: Number of experiences to sample
            
        Returns:
            Tuple of batched experiences
        """
        batch = random.sample(self.buffer, batch_size)
        state, action, reward, next_state, done = zip(*batch)
        
        return (
            np.array(state),
            np.array(action),
            np.array(reward, dtype=np.float32),
            np.array(next_state),
            np.array(done, dtype=np.float32)
        )
    
    def __len__(self) -> int:
        """Get current size of buffer."""
        return len(self.buffer)


class DQN(ModelBase):
    """Deep Q-Network implementation.
    
    This is a simplified DQN implementation for demonstration purposes.
    In a real implementation, you would use a deep learning framework
    like PyTorch or TensorFlow.
    """
    
    model_type = "DQN"
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize DQN model.
        
        Args:
            config: Model configuration
        """
        super().__init__(config) # This sets self.hyperparameters and self.logger

        # Store the full config for get_model_state and registry
        self.model_init_config = config.copy() # Store a copy
        self.logger.debug(f"DQN.__init__: Stored model_init_config: {self.model_init_config}")
        
        # Extract hyperparameters (already done by super().__init__, but ensure they are accessible)
        # self.hyperparams is set by super().__init__(config)
        self.hidden_layers = self.hyperparameters.get("hidden_layers", [256, 128, 64])
        self.activation = self.hyperparameters.get("activation", "relu")
        self.dropout_rate = self.hyperparameters.get("dropout_rate", 0.2)
        self.double_dqn = self.hyperparameters.get("double_dqn", True)
        self.dueling_dqn = self.hyperparameters.get("dueling_dqn", False)
        self.prioritized_replay = self.hyperparameters.get("prioritized_replay", True)
        
        # Learning and Optimizer settings from hyperparameters
        self.learning_rate = self.hyperparameters.get("learning_rate", 0.001)
        self.beta1 = self.hyperparameters.get("adam_beta1", 0.9)
        self.beta2 = self.hyperparameters.get("adam_beta2", 0.999)
        self.epsilon = self.hyperparameters.get("adam_epsilon", 1e-8) # Adam epsilon, not exploration epsilon

        # Memory settings from hyperparameters
        self.memory_size = self.hyperparameters.get("memory_size", 10000)
        self.update_frequency = self.hyperparameters.get("update_frequency", 4)
        self.target_update_frequency = self.hyperparameters.get("target_update_frequency", 100)
        
        # Initialize components
        self.replay_buffer = ReplayBuffer(self.memory_size)
        self.q_network = None
        self.target_network = None
        self.optimizer = None
        self.steps = 0
        self.episodes = 0
        
        from ...evaluation.metrics import MetricsCalculator
        self.metrics_calculator = MetricsCalculator(self.model_init_config.get("metrics_config", {}))

        # Structural parameters from model_init_config (not hyperparameters dict)
        self.input_dim = self.model_init_config.get("input_dim")
        self.output_dim = self.model_init_config.get("output_dim")

        # Initialize self.hidden_dims, preferring model_init_config, then hyperparameters
        self.hidden_dims = self.model_init_config.get("hidden_dims")
        if self.hidden_dims is None:
            # Fallback to hidden_layers from hyperparameters if hidden_dims not in model_init_config
            self.hidden_dims = self.hyperparameters.get("hidden_layers", [256, 128, 64])
            self.logger.info(f"DQN.__init__: 'hidden_dims' not in model_init_config. Using 'hidden_layers' from hyperparameters for self.hidden_dims: {self.hidden_dims}")
        else:
            self.logger.info(f"DQN.__init__: Set self.hidden_dims from model_init_config: {self.hidden_dims}")

        # self.hidden_layers is already set from self.hyperparameters (line 87).
        # Log if the source for self.hidden_layers (hyperparameters) and final self.hidden_dims differ.
        # The network build process will use self.hidden_dims.
        if self.hyperparameters.get("hidden_layers") != self.hidden_dims:
            self.logger.warning(
                f"DQN.__init__: Value for 'hidden_layers' from hyperparameters ({self.hyperparameters.get('hidden_layers')}) "
                f"differs from final 'self.hidden_dims' ({self.hidden_dims}). "
                f"The network will be built using self.hidden_dims: {self.hidden_dims}."
            )
        
        self.input_shape: Optional[Tuple[int, ...]] = None
        self.output_shape: Optional[Tuple[int, ...]] = None
        self.n_actions: Optional[int] = None

        if self.input_dim is not None:
            self.input_shape = (self.input_dim,)
            self.logger.info(f"DQN.__init__: Set input_shape from input_dim: {self.input_shape}")
        elif hasattr(self, 'observation_space') and self.observation_space is not None and hasattr(self.observation_space, 'shape'):
            self.input_shape = self.observation_space.shape
            self.logger.info(f"DQN.__init__: Set input_shape from observation_space: {self.input_shape}")
        else:
            self.logger.warning("DQN.__init__: input_shape could not be determined.")

        if self.output_dim is not None:
            self.output_shape = (self.output_dim,)
            self.n_actions = self.output_dim
            self.logger.info(f"DQN.__init__: Set output_shape and n_actions from output_dim: {self.output_shape}, {self.n_actions}")
        elif hasattr(self, 'action_space') and self.action_space is not None and hasattr(self.action_space, 'n'):
            self.output_shape = (self.action_space.n,)
            self.n_actions = self.action_space.n
            self.logger.info(f"DQN.__init__: Set output_shape from action_space: {self.output_shape}, n_actions: {self.n_actions}")
        else:
            self.logger.warning("DQN.__init__: output_shape and n_actions could not be determined.")
        
        self.is_model_built = False # Explicitly set to False initially
        
        # self.config is used by ModelRegistry. It should be the model_init_config.
        # This is already set by super().__init__(config) if config is passed,
        # but we ensure it's our copy.
        self.config = self.model_init_config


        # Training history - expanded
        self.training_history = {
            "episode_rewards": [], "episode_lengths": [], "losses": [], "epsilon_values": [],
            "rl_episode_reward": [], "rl_episode_length": [], "rl_loss": [], "rl_entropy": [],
            "rl_learning_rate": [], "rl_value_estimates": [], "rl_td_error": [], "rl_kl_divergence": [],
            "rl_explained_variance": [], "rl_success_rate": [], "portfolio_values": [],
            "episode_returns": [], "trades": [], "sharpe_ratio": [], "sortino_ratio": [],
            "max_drawdown": [], "calmar_ratio": [], "win_rate": [], "profit_factor": [],
            "average_win": [], "average_loss": [], "expectancy": [], "volatility": [],
            "downside_deviation": [], "value_at_risk": [], "conditional_value_at_risk": [],
            "pnl": [], "pnl_percentage": [], "total_return": []
        }
    
    def build(self, input_shape: Tuple[int, ...], output_shape: Tuple[int, ...]) -> None:
        """Build the Q-network architecture.
        
        Args:
            input_shape: Shape of state input
            output_shape: Shape of action output (number of actions)
        """
        self.input_shape = input_shape
        self.output_shape = output_shape
        self.n_actions = output_shape[0] if len(output_shape) > 0 else output_shape
        
        # Initialize networks with proper structure
        self._initialize_networks(input_shape, output_shape)
        
        # Copy weights to target network
        self._update_target_network()
    
    def _initialize_networks(self, input_shape: Tuple[int, ...], output_shape: Tuple[int, ...]) -> None:
        """Initialize Q-network and target network structures.
        
        Args:
            input_shape: Shape of state input
            output_shape: Shape of action output
        """
        # Initialize Q-network
        q_network_weights = self._initialize_weights(input_shape, output_shape)
        optimizer_m_state = {key: np.zeros_like(val) for key, val in q_network_weights.items()}
        optimizer_v_state = {key: np.zeros_like(val) for key, val in q_network_weights.items()}
        
        self.q_network = {
            "weights": q_network_weights,
            "input_shape": input_shape,
            "output_shape": output_shape,
            "optimizer_state": {
                "t": 0, # Adam timestep
                "m": optimizer_m_state,
                "v": optimizer_v_state
            }
        }
        
        # Initialize target network (does not need optimizer state)
        self.target_network = {
            "weights": self._initialize_weights(input_shape, output_shape), # Fresh set of weights
            "input_shape": input_shape,
            "output_shape": output_shape
        }
    
    def _initialize_weights(self, input_shape: Tuple[int, ...],
                          output_shape: Tuple[int, ...]) -> Dict[str, np.ndarray]:
        """Initialize network weights.
        
        Args:
            input_shape: Input shape
            output_shape: Output shape
            
        Returns:
            Dictionary of weight matrices
        """
        # Simplified weight initialization
        input_size = np.prod(input_shape)
        output_size = np.prod(output_shape)
        
        weights = {}
        prev_size = input_size
        
        # Hidden layers
        for i, hidden_size in enumerate(self.hidden_dims):
            weights[f"W{i}"] = np.random.randn(prev_size, hidden_size) * 0.01
            weights[f"b{i}"] = np.zeros(hidden_size)
            prev_size = hidden_size
        
        # Output layer
        weights["W_out"] = np.random.randn(prev_size, output_size) * 0.01
        weights["b_out"] = np.zeros(output_size)
        
        return weights
    
    def _update_target_network(self) -> None:
        """Update target network with current Q-network weights."""
        if self.q_network and self.target_network:
            self.target_network["weights"] = {
                k: v.copy() for k, v in self.q_network["weights"].items()
            }
    
    def _forward(self, state: np.ndarray, network: Dict[str, Any]) -> np.ndarray:
        """Forward pass through network.
        
        Args:
            state: Input state
            network: Network to use (q_network or target_network)
            
        Returns:
            Q-values for all actions
        """
        # Ensure network has proper structure
        if not network or "weights" not in network:
            raise ValueError("Network structure is invalid or not initialized")
        
        weights = network["weights"]
        
        # Validate weights structure
        if not isinstance(weights, dict):
            raise ValueError("Network weights must be a dictionary")
        
        # Check if required weight keys exist
        for i in range(len(self.hidden_dims)): # Use hidden_dims
            if f"W{i}" not in weights or f"b{i}" not in weights:
                raise KeyError(f"Missing weight key W{i} or b{i} in network weights. Available keys: {list(weights.keys())}")
        
        if "W_out" not in weights or "b_out" not in weights:
            raise KeyError(f"Missing output layer weights. Available keys: {list(weights.keys())}")
        
        # Simplified forward pass
        x = state.flatten()
        self.logger.debug(f"_forward: input state flattened sample: {x[:5]}")
        if np.isnan(x).any(): self.logger.warning(f"_forward: NaN in input state x: {x}")

        # Hidden layers
        for i in range(len(self.hidden_dims)): # Use hidden_dims
            x_prev = x
            W = weights[f"W{i}"]
            b = weights[f"b{i}"]
            if np.isnan(W).any(): self.logger.warning(f"_forward: NaN in W{i}")
            if np.isnan(b).any(): self.logger.warning(f"_forward: NaN in b{i}")
            
            x = np.dot(x_prev, W) + b
            self.logger.debug(f"_forward: after layer {i} (pre-activation) x sample: {x[:5]}")
            if np.isnan(x).any():
                self.logger.warning(f"_forward: NaN in x after layer {i} (pre-activation). x_prev: {x_prev[:5]}, W: {W[:2,:2]}, b: {b[:5]}") # Log parts of W and b
                return x # Return early if NaN detected

            # ReLU activation
            if self.activation == "relu":
                x = np.maximum(0, x)
            elif self.activation == "tanh":
                x = np.tanh(x)
            self.logger.debug(f"_forward: after layer {i} (post-activation) x sample: {x[:5]}")
            if np.isnan(x).any():
                self.logger.warning(f"_forward: NaN in x after layer {i} (post-activation)")
                return x # Return early
        
        # Output layer
        W_out = weights["W_out"]
        b_out = weights["b_out"]
        if np.isnan(W_out).any(): self.logger.warning(f"_forward: NaN in W_out")
        if np.isnan(b_out).any(): self.logger.warning(f"_forward: NaN in b_out")

        q_values = np.dot(x, W_out) + b_out
        self.logger.debug(f"_forward: final q_values sample: {q_values[:5]}")
        if np.isnan(q_values).any():
            self.logger.error(f"_forward: NaN in final q_values. x: {x[:5]}, W_out: {W_out[:2,:2]}, b_out: {b_out[:5]}")
        
        return q_values
    
    def predict(self, data: Any, **kwargs) -> Any:
        """Predict Q-values for given states.
        
        Args:
            data: State or batch of states
            **kwargs: Additional arguments (e.g., use_target_network)
            
        Returns:
            Q-values or selected actions
        """
        if not self.q_network:
            raise ValueError("Model must be built before prediction")
        
        use_target = kwargs.get("use_target_network", False)
        network = self.target_network if use_target else self.q_network
        
        # Handle single state or batch
        if isinstance(data, np.ndarray):
            if len(data.shape) == len(self.input_shape):
                # Single state
                return self._forward(data, network)
            else:
                # Batch of states
                return np.array([self._forward(s, network) for s in data])
        else:
            raise ValueError("Data must be numpy array")
    
    def select_action(self, state: np.ndarray, epsilon: float = 0.0) -> int:
        """Select action using epsilon-greedy policy.
        
        Args:
            state: Current state
            epsilon: Exploration rate
            
        Returns:
            Selected action
        """
        if np.random.random() < epsilon:
            return np.random.randint(self.n_actions)
        else:
            q_values = self.predict(state)
            
            # Track Q-values for RL metrics calculation
            if not hasattr(self, '_episode_q_values'):
                self._episode_q_values = []
            
            # Store the max Q-value for this state
            if isinstance(q_values, np.ndarray) and len(q_values) > 0:
                max_q_value = float(np.max(q_values))
                self._episode_q_values.append(max_q_value)
            
            return np.argmax(q_values)
    
    def train(self, train_data: Any, validation_data: Optional[Any] = None,
              **kwargs) -> Dict[str, Any]:
        """Train the DQN model.
        
        Args:
            train_data: Training environment or data
            validation_data: Optional validation data
            **kwargs: Additional training arguments
            
        Returns:
            Training history and metrics
        """
        # Ensure model is built
        if self.q_network is None or self.target_network is None:
            raise ValueError("Model must be built before training. Call build() first.")
        
        # Check and reinitialize if weights are missing
        if "weights" not in self.q_network or not self.q_network["weights"]:
            print("WARNING: Q-network weights were not properly initialized. Reinitializing...")
            if hasattr(self, 'input_shape') and hasattr(self, 'output_shape'):
                self._initialize_networks(self.input_shape, self.output_shape)
            else:
                raise ValueError("Cannot reinitialize networks: input_shape and output_shape not set")
        
        # Extract training parameters
        episodes = kwargs.get("episodes", 100)
        batch_size = kwargs.get("batch_size", 32)
        gamma = kwargs.get("gamma", 0.99)
        epsilon_start = kwargs.get("epsilon_start", 1.0)
        epsilon_end = kwargs.get("epsilon_end", 0.01)
        epsilon_decay = kwargs.get("epsilon_decay", 0.995)
        learning_rate = kwargs.get("learning_rate", 0.001)
        
        epsilon = epsilon_start

        # Reward and portfolio parameters
        initial_episode_cash = 100000.0
        transaction_cost_rate = 0.001
        invalid_action_penalty = -1.0
        hold_cash_reward = -0.005 # Small penalty for holding cash
        unrealized_pnl_reward_scaling_factor = 0.1
        close_price_index = 3 # Assuming OHLCV, Close is at index 3

        # Enhanced logging for train_data
        self.logger.info(f"DQN.train() received train_data of type: {type(train_data)}")
        if hasattr(train_data, 'shape'):
            self.logger.info(f"DQN.train() received train_data with shape: {train_data.shape}")
        else:
            self.logger.info(f"DQN.train() received train_data that has no shape attribute.")
        if hasattr(train_data, 'ndim'):
            self.logger.info(f"DQN.train() received train_data with ndim: {train_data.ndim}")
        else:
            self.logger.info(f"DQN.train() received train_data that has no ndim attribute.")

        if not isinstance(train_data, np.ndarray) or train_data.ndim != 2:
            self.logger.error(f"Validation failed for train_data: type is {type(train_data)}, ndim is {getattr(train_data, 'ndim', 'N/A')}, is_ndarray: {isinstance(train_data, np.ndarray)}")
            raise ValueError("train_data must be a 2D numpy array of features.")
        
        self.logger.info(f"DQN.train() train_data shape[1]: {train_data.shape[1]}, close_price_index: {close_price_index}")
        if train_data.shape[1] <= close_price_index:
            raise ValueError(f"close_price_index {close_price_index} is out of bounds for train_data with shape {train_data.shape}")

        # Training loop
        for episode in range(episodes):
            cash = initial_episode_cash
            current_position_units = 0.0
            entry_price = 0.0
            previous_unrealized_pnl = 0.0
            
            episode_reward = 0
            episode_length = 0
            losses = []
            
            # Track portfolio values and trades for metrics calculation
            episode_portfolio_values = [initial_episode_cash]
            episode_trades = []
            
            # Iterate through the training data for the episode
            # Ensure we have enough data for a next_state
            for step_idx in range(len(train_data) - 1):
                state = train_data[step_idx]
                current_price = state[close_price_index]
                next_state = train_data[step_idx + 1]
                next_price = next_state[close_price_index] # Used for liquidation if done

                action = self.select_action(state, epsilon)
                reward = 0.0

                # Trading Logic (0: Hold, 1: Buy, 2: Sell)
                if action == 0: # Hold
                    if current_position_units > 0:
                        unrealized_pnl_at_step = (current_price - entry_price) * current_position_units
                        change_in_unrealized_pnl = unrealized_pnl_at_step - previous_unrealized_pnl
                        reward = change_in_unrealized_pnl * unrealized_pnl_reward_scaling_factor
                        previous_unrealized_pnl = unrealized_pnl_at_step
                    else: # Holding cash
                        reward = hold_cash_reward
                        previous_unrealized_pnl = 0.0
                elif action == 1: # Buy
                    # Check if affordable and not already in position
                    if cash >= current_price * (1 + transaction_cost_rate) and current_position_units == 0 and current_price > 0:
                        units_to_buy = cash / (current_price * (1 + transaction_cost_rate))
                        cost = units_to_buy * current_price * transaction_cost_rate
                        
                        cash -= (units_to_buy * current_price) + cost
                        current_position_units = units_to_buy
                        entry_price = current_price
                        reward = -cost # Negative reward for transaction cost
                        previous_unrealized_pnl = 0.0
                        
                        # Record buy trade for metrics
                        episode_trades.append({
                            'type': 'buy',
                            'price': current_price,
                            'units': units_to_buy,
                            'cost': cost,
                            'pnl': -cost  # Transaction cost as negative PnL
                        })
                    else:
                        reward = invalid_action_penalty
                elif action == 2: # Sell
                    if current_position_units > 0:
                        realized_pnl = (current_price - entry_price) * current_position_units
                        transaction_value = current_position_units * current_price
                        cost = transaction_value * transaction_cost_rate
                        
                        reward = realized_pnl - cost
                        cash += transaction_value - cost
                        
                        # Record sell trade for metrics
                        episode_trades.append({
                            'type': 'sell',
                            'price': current_price,
                            'units': current_position_units,
                            'cost': cost,
                            'pnl': realized_pnl - cost  # Net PnL after transaction costs
                        })
                        
                        current_position_units = 0.0
                        entry_price = 0.0
                        previous_unrealized_pnl = 0.0
                    else:
                        reward = invalid_action_penalty
                
                done = (step_idx == len(train_data) - 2) # Episode ends at the second to last step

                # If done and holding a position, liquidate
                if done and current_position_units > 0:
                    # PnL from liquidation at next_price (which is the last available price)
                    liquidation_pnl = (next_price - entry_price) * current_position_units
                    liquidation_cost = (current_position_units * next_price) * transaction_cost_rate
                    # Add this to the reward of the last action
                    reward += liquidation_pnl - liquidation_cost
                    cash += (current_position_units * next_price) - liquidation_cost
                    current_position_units = 0 # Position closed
                
                self.replay_buffer.push(state, action, reward, next_state, done)
                
                episode_reward += reward
                episode_length += 1
                
                # Track portfolio value for metrics calculation
                current_portfolio_value = cash + (current_position_units * current_price if current_position_units > 0 else 0)
                episode_portfolio_values.append(current_portfolio_value)
                
                if len(self.replay_buffer) >= batch_size and self.steps % self.update_frequency == 0:
                    loss = self._train_step(batch_size, gamma) # learning_rate removed
                    losses.append(loss)
                
                if self.steps % self.target_update_frequency == 0:
                    self._update_target_network()
                
                self.steps += 1
                
                if done:
                    break # End of episode
            
            epsilon = max(epsilon_end, epsilon * epsilon_decay)
            
            # Calculate trading metrics for this episode using MetricsCalculator
            if len(episode_portfolio_values) > 1:
                # Calculate returns from portfolio values
                episode_returns = []
                for i in range(1, len(episode_portfolio_values)):
                    if episode_portfolio_values[i-1] > 0:
                        ret = (episode_portfolio_values[i] - episode_portfolio_values[i-1]) / episode_portfolio_values[i-1]
                        episode_returns.append(ret)
                
                # Use MetricsCalculator to compute all trading metrics
                try:
                    metrics = self.metrics_calculator.calculate_all_metrics(
                        portfolio_values=episode_portfolio_values,
                        returns=episode_returns,
                        trades=episode_trades
                    )
                    
                    # Store metrics in training history
                    for metric_name, metric_value in metrics.items():
                        if metric_name in self.training_history:
                            self.training_history[metric_name].append(metric_value)
                    
                    # Store additional episode data
                    self.training_history["portfolio_values"].append(episode_portfolio_values)
                    self.training_history["episode_returns"].append(episode_returns)
                    self.training_history["trades"].append(episode_trades)
                    
                except Exception as e:
                    self.logger.warning(f"Failed to calculate trading metrics for episode {episode}: {e}")
                    # Store NaN values for failed calculations
                    for metric_name in ["sharpe_ratio", "sortino_ratio", "max_drawdown", "calmar_ratio",
                                       "win_rate", "profit_factor", "average_win", "average_loss",
                                       "expectancy", "volatility", "downside_deviation", "value_at_risk",
                                       "conditional_value_at_risk", "pnl", "pnl_percentage", "total_return"]:
                        if metric_name in self.training_history:
                            self.training_history[metric_name].append(np.nan)
            else:
                # No portfolio data, store NaN values
                for metric_name in ["sharpe_ratio", "sortino_ratio", "max_drawdown", "calmar_ratio",
                                   "win_rate", "profit_factor", "average_win", "average_loss",
                                   "expectancy", "volatility", "downside_deviation", "value_at_risk",
                                   "conditional_value_at_risk", "pnl", "pnl_percentage", "total_return"]:
                    if metric_name in self.training_history:
                        self.training_history[metric_name].append(np.nan)
                
                self.training_history["portfolio_values"].append([])
                self.training_history["episode_returns"].append([])
                self.training_history["trades"].append([])
            
            self.episodes += 1
            
            # Store core RL metrics
            self.training_history["episode_rewards"].append(episode_reward)
            self.training_history["episode_lengths"].append(episode_length)
            if losses: # only extend if there are losses to add
                self.training_history["losses"].extend(losses)
            self.training_history["epsilon_values"].append(epsilon)
            
            # Calculate and store additional RL-specific metrics
            self._calculate_rl_metrics(episode_reward, episode_length, losses, epsilon,
                                     episode_portfolio_values, episode_trades)
            
            if episode % 10 == 0:
                # Ensure there are rewards to average
                avg_reward = np.mean(self.training_history["episode_rewards"][-10:]) if self.training_history["episode_rewards"] else 0.0
                print(f"Episode {self.episodes}, Avg Reward: {avg_reward:.2f}, Epsilon: {epsilon:.3f}, Steps: {self.steps}, Buffer: {len(self.replay_buffer)}")

        self.is_trained = True
        self.update_metadata({
            "training_episodes": self.episodes, # Use self.episodes
            "final_epsilon": epsilon,
            "total_steps": self.steps
        })
        
        return self.training_history

    def _train_step(self, batch_size: int, gamma: float) -> float:
        """Perform one training step with backpropagation and Adam optimizer.
        
        Args:
            batch_size: Batch size for training
            gamma: Discount factor
            
        Returns:
            Training loss
        """
        # Sample batch from replay buffer
        states, actions, rewards, next_states, dones = self.replay_buffer.sample(batch_size)
        
        # Compute target Q-values (using Double DQN logic if enabled)
        if self.double_dqn:
            next_q_values_online = self.predict(next_states) # Q(s', a; theta)
            next_actions = np.argmax(next_q_values_online, axis=1)
            next_q_values_target = self.predict(next_states, use_target_network=True) # Q(s', a; theta_target)
            next_q_selected = next_q_values_target[np.arange(batch_size), next_actions]
        else:
            next_q_values_target = self.predict(next_states, use_target_network=True)
            next_q_selected = np.max(next_q_values_target, axis=1)
        
        targets = rewards + gamma * next_q_selected * (1 - dones)

        # --- Forward pass for current states to get activations and Q-values for selected actions ---
        # Assuming states is (batch_size, num_features)
        X_batch = states
        
        # Ensure q_network and weights are available
        if self.q_network is None or "weights" not in self.q_network:
            self.logger.error("_train_step: Q-network or its weights are not initialized. Cannot proceed.")
            return np.nan # Or raise an error

        current_weights = self.q_network["weights"]
        
        # Generalized check for all weight matrices
        all_param_keys_for_check = []
        for i in range(len(self.hidden_dims)): # Use hidden_dims
            all_param_keys_for_check.extend([f"W{i}", f"b{i}"])
        all_param_keys_for_check.extend(["W_out", "b_out"])

        missing_keys = [key for key in all_param_keys_for_check if current_weights.get(key) is None]
        if missing_keys:
            self.logger.error(f"_train_step: One or more weight matrices are missing: {missing_keys}. Available keys: {list(current_weights.keys())}")
            return np.nan

        # Generalized forward pass for training (caching activations and pre-activations)
        A_layers = {}  # Activations: A_layers[l_idx] = activation of layer l_idx
        Z_layers = {}  # Pre-activations: Z_layers[l_idx] = pre-activation of layer l_idx
        
        A_layers[-1] = X_batch # Input layer activation (index -1 for convenience)

        # Hidden layers
        current_A = X_batch
        for l_idx in range(len(self.hidden_dims)): # Use hidden_dims
            Wl = current_weights[f"W{l_idx}"]
            bl = current_weights[f"b{l_idx}"]
            
            Z_layers[l_idx] = current_A @ Wl + bl
            # Consistent with backprop assumption of ReLU for hidden layers during training
            current_A = np.maximum(0, Z_layers[l_idx])
            A_layers[l_idx] = current_A
            if np.isnan(Z_layers[l_idx]).any() or np.isnan(A_layers[l_idx]).any():
                self.logger.warning(f"_train_step: NaN detected in hidden layer {l_idx}. Z: {np.isnan(Z_layers[l_idx]).any()}, A: {np.isnan(A_layers[l_idx]).any()}")
                # return np.nan # Optional: return early if NaN

        # Output layer
        W_out = current_weights["W_out"]
        b_out = current_weights["b_out"]
        # The input to the output layer is the activation of the last hidden layer,
        # or X_batch if there are no hidden layers.
        input_to_output_layer = A_layers[len(self.hidden_dims) - 1] if self.hidden_dims else X_batch # Use hidden_dims
        
        Z_layers["out"] = input_to_output_layer @ W_out + b_out
        current_q_values_all_actions = Z_layers["out"] # Q-values are pre-activation of output
        
        current_q_selected_for_loss = current_q_values_all_actions[np.arange(batch_size), actions]
        
        # Compute loss
        loss = np.mean((targets - current_q_selected_for_loss) ** 2)
        if np.isnan(loss) or np.isinf(loss):
            self.logger.error(f"_train_step: Loss is NaN or Inf ({loss}) before updates. Targets: {targets[:3]}, Q_selected: {current_q_selected_for_loss[:3]}")
            return float(loss) # Return early if loss is invalid

        # --- Backward pass ---
        grads = {}
        num_hidden_layers = len(self.hidden_dims) # Use hidden_dims

        # Gradient of loss w.r.t. Z_out (output layer pre-activation)
        # dL/dQ_selected * dQ_selected/dZ_out (where dQ_selected/dZ_out is 1 for the selected action, 0 otherwise)
        dloss_dZ_out = np.zeros_like(current_q_values_all_actions)
        # The factor of 2 comes from (y-y_hat)^2, derivative is 2(y_hat-y).
        # Division by batch_size for mean squared error.
        dloss_dZ_out[np.arange(batch_size), actions] = 2 * (current_q_selected_for_loss - targets) / batch_size
        
        # Gradients for output layer (W_out, b_out)
        # Input to output layer was A_layers[num_hidden_layers - 1] or X_batch if no hidden layers
        A_input_to_output = A_layers.get(num_hidden_layers - 1, X_batch) # Use X_batch if num_hidden_layers is 0
        
        grads["W_out"] = A_input_to_output.T @ dloss_dZ_out
        grads["b_out"] = np.sum(dloss_dZ_out, axis=0)
        
        # Initialize dloss_dA_prev for backpropagation through hidden layers
        # This is dL/dA_last_hidden_layer
        dloss_dA_prev = dloss_dZ_out @ current_weights["W_out"].T
        
        # Loop backward through hidden layers
        for l_idx in range(num_hidden_layers - 1, -1, -1):
            # Gradient of loss w.r.t. Zl (hidden layer l_idx pre-activation)
            # dL/dZl = dL/dAl * dAl/dZl
            # dAl/dZl is derivative of ReLU: 1 if Zl > 0, else 0
            dRelu_dZl = (Z_layers[l_idx] > 0) * 1.0
            dloss_dZl = dloss_dA_prev * dRelu_dZl
            
            # Gradients for hidden layer l_idx (Wl, bl)
            # Input to this layer was A_layers[l_idx - 1] (or X_batch if l_idx is 0)
            A_input_to_current_hidden = A_layers.get(l_idx - 1, X_batch) # Use X_batch if l_idx-1 is -1
            
            grads[f"W{l_idx}"] = A_input_to_current_hidden.T @ dloss_dZl
            grads[f"b{l_idx}"] = np.sum(dloss_dZl, axis=0)
            
            # Update dloss_dA_prev for the next iteration (i.e., for layer l_idx-1)
            # This is dL/dA_{l_idx-1} = dL/dZl * dZl/dA_{l_idx-1} = dL/dZl * Wl.T
            if l_idx > 0: # No need to compute for A_input (A_layers[-1])
                dloss_dA_prev = dloss_dZl @ current_weights[f"W{l_idx}"].T

        # Log gradient norms (optional, for debugging)
        # self.logger.debug(f"Grad norms: W0={np.linalg.norm(grads['W0']):.2e}, b0={np.linalg.norm(grads['b0']):.2e}, W_out={np.linalg.norm(grads['W_out']):.2e}, b_out={np.linalg.norm(grads['b_out']):.2e}")

        # --- Adam Optimizer Update ---
        if "optimizer_state" not in self.q_network:
            self.logger.error("_train_step: Optimizer state not found in q_network. Cannot apply Adam.")
            return float(loss) # Or re-initialize optimizer state here if appropriate

        opt_state = self.q_network["optimizer_state"]
        opt_state["t"] += 1
        t = opt_state["t"]
        
        param_keys_for_adam = []
        for i in range(len(self.hidden_dims)): # Use hidden_dims
            param_keys_for_adam.extend([f"W{i}", f"b{i}"])
        param_keys_for_adam.extend(["W_out", "b_out"])

        for param_key in param_keys_for_adam:
            if param_key not in grads:
                self.logger.warning(f"_train_step: Gradient for {param_key} not found. Skipping update for this param.")
                continue
            
            grad_p = grads[param_key]
            
            # Ensure optimizer state m and v exist for this param_key
            if param_key not in opt_state["m"] or param_key not in opt_state["v"]:
                 self.logger.error(f"_train_step: Optimizer state m or v missing for {param_key}. Re-initializing for this param.")
                 opt_state["m"][param_key] = np.zeros_like(grad_p)
                 opt_state["v"][param_key] = np.zeros_like(grad_p)

            opt_state["m"][param_key] = self.beta1 * opt_state["m"][param_key] + (1 - self.beta1) * grad_p
            opt_state["v"][param_key] = self.beta2 * opt_state["v"][param_key] + (1 - self.beta2) * (grad_p ** 2)
            
            m_hat = opt_state["m"][param_key] / (1 - self.beta1 ** t)
            v_hat = opt_state["v"][param_key] / (1 - self.beta2 ** t)
            
            weight_update = self.learning_rate * m_hat / (np.sqrt(v_hat) + self.epsilon)
            
            # Sanity check for NaN/Inf in weight_update
            if np.isnan(weight_update).any() or np.isinf(weight_update).any():
                self.logger.error(f"_train_step: NaN/Inf in weight_update for {param_key}. Skipping update for this param. m_hat_sample: {m_hat.flatten()[:2]}, v_hat_sample: {v_hat.flatten()[:2]}")
                continue

            current_weights[param_key] -= weight_update

            # Sanity check for NaN/Inf in weights after update
            if np.isnan(current_weights[param_key]).any() or np.isinf(current_weights[param_key]).any():
                self.logger.critical(f"_train_step: CRITICAL - Weights[{param_key}] became NaN/Inf AFTER Adam update. This should not happen with proper Adam. Investigate. Update was: {weight_update.flatten()[:2]}")
                # Potentially revert or handle, but for now, just log critically.
        
        self.logger.debug(f"_train_step: Adam update applied. Loss: {loss:.4f}")
        return float(loss)
    
    def evaluate(self, test_data: Any, **kwargs) -> Dict[str, float]:
        """Evaluate the model on test data.
        
        Args:
            test_data: Test environment or data
            **kwargs: Additional evaluation arguments
            
        Returns:
            Dictionary of evaluation metrics
        """
        if not self.is_trained:
            raise ValueError("Model must be trained before evaluation")
        
        # Simulate evaluation
        n_episodes = kwargs.get("n_episodes", 10)
        
        episode_rewards = []
        episode_lengths = []
        
        for episode in range(n_episodes):
            episode_reward = 0
            episode_length = 0
            
            # Simulate test episode
            for step in range(100):
                state = np.random.randn(*self.input_shape)
                action = self.select_action(state, epsilon=0.0)  # No exploration
                reward = np.random.randn()
                done = step == 99 or np.random.random() < 0.01
                
                episode_reward += reward
                episode_length += 1
                
                if done:
                    break
            
            episode_rewards.append(episode_reward)
            episode_lengths.append(episode_length)
        
        return {
            "mean_episode_reward": float(np.mean(episode_rewards)),
            "std_episode_reward": float(np.std(episode_rewards)),
            "mean_episode_length": float(np.mean(episode_lengths)),
            "min_episode_reward": float(np.min(episode_rewards)),
            "max_episode_reward": float(np.max(episode_rewards))
        }
    
    def get_model_state(self) -> Dict[str, Any]:
        """Get the current state of the model for saving."""
        self.logger.debug(f"DQN.get_model_state called. self.q_network is {'set' if self.q_network else 'None'}")
        model_weights = None
        if self.q_network and isinstance(self.q_network, dict) and "weights" in self.q_network:
            model_weights = self.q_network["weights"]
            self.logger.debug(f"DQN.get_model_state: Extracted weights from self.q_network['weights']. Keys: {list(model_weights.keys()) if model_weights else 'None'}")
        
        # Return the config used to initialize, plus current weights and built status
        # This ensures ModelRegistry can re-create the model structure correctly.
        state_to_save = {
            "creation_config": self.model_init_config, # The full config dict used at __init__
            "model_weights": model_weights,
            "is_model_built": self.is_model_built
        }
        self.logger.debug(f"DQN.get_model_state: Returning state with keys: {list(state_to_save.keys())}")
        return state_to_save

    def set_model_state(self, state: Dict[str, Any]):
        """Load the model state from a dictionary."""
        self.logger.info(f"DQN.set_model_state called. Current self.is_model_built: {self.is_model_built}")
        self.logger.debug(f"DQN.set_model_state: Received state keys: {list(state.keys())}")

        # Prioritize creation_config from state for dimensions if this instance wasn't fully initialized
        # This is key for when ModelRegistry re-creates the model
        creation_config = state.get("creation_config")
        if isinstance(creation_config, dict):
            self.logger.info("DQN.set_model_state: Using 'creation_config' from state to ensure dimensions.")
            self.input_dim = creation_config.get("input_dim", self.input_dim)
            self.output_dim = creation_config.get("output_dim", self.output_dim)
            # Explicitly handle hidden_dims
            loaded_hidden_dims = creation_config.get("hidden_dims")
            if loaded_hidden_dims is not None:
                self.hidden_dims = loaded_hidden_dims
                self.logger.info(f"DQN.set_model_state: Updated self.hidden_dims from creation_config: {self.hidden_dims}")
            else:
                # If not in creation_config, self.hidden_dims should have been set by __init__.
                # We log if it's unexpectedly missing, then try to recover.
                if not hasattr(self, 'hidden_dims'): # Check existence first
                    self.logger.error("DQN.set_model_state: CRITICAL - self.hidden_dims was not set by __init__ and also not found in creation_config. Attempting recovery.")
                    current_hyperparameters = getattr(self, 'hyperparameters', {})
                    self.hidden_dims = current_hyperparameters.get("hidden_layers", [256, 128, 64])
                    self.logger.warning(f"DQN.set_model_state: Recovered self.hidden_dims to: {self.hidden_dims}")
                # If hasattr is true, self.hidden_dims already exists from __init__.
                # If self.hidden_dims was None after __init__ (which current __init__ logic should prevent),
                # it would remain None here, and build() would use that.
                # This is acceptable as build() will use whatever self.hidden_dims is.
            # Restore hyperparameters from the creation_config as well
            self.hyperparameters = creation_config.get("hyperparameters", self.hyperparameters)
            self.logger.info(f"DQN.set_model_state: Hyperparameters set from creation_config: {self.hyperparameters}")


            if self.input_dim and (self.input_shape is None or self.input_shape[0] != self.input_dim):
                self.input_shape = (self.input_dim,)
                self.logger.info(f"DQN.set_model_state: Set/Updated self.input_shape={self.input_shape} from creation_config.")
            if self.output_dim and (self.output_shape is None or self.output_shape[0] != self.output_dim or self.n_actions is None):
                self.output_shape = (self.output_dim,)
                self.n_actions = self.output_dim
                self.logger.info(f"DQN.set_model_state: Set/Updated self.output_shape={self.output_shape}, self.n_actions={self.n_actions} from creation_config.")
        else: # Fallback if creation_config is not there (e.g. raw HPO params)
            self.logger.info("DQN.set_model_state: 'creation_config' not in state. Using direct state keys for dimensions if available.")
            if 'input_dim' in state and (self.input_dim != state['input_dim'] or self.input_shape is None):
                self.input_dim = state['input_dim']
                self.input_shape = (self.input_dim,)
            if 'output_dim' in state and (self.output_dim != state['output_dim'] or self.output_shape is None):
                self.output_dim = state['output_dim']
                self.output_shape = (self.output_dim,)
                self.n_actions = self.output_dim
            if 'hidden_dims' in state: self.hidden_dims = state['hidden_dims']
            if 'hyperparameters' in state: self.hyperparameters.update(state['hyperparameters'])


        # Build network if not already built or if essential shapes are now available
        if not self.q_network:
            if self.input_shape and self.output_shape:
                self.logger.info(f"DQN.set_model_state: Calling self.build() with input_shape={self.input_shape}, output_shape={self.output_shape}, hidden_dims={self.hidden_dims}.")
                self.build(self.input_shape, self.output_shape) # Pass shapes to build
            else:
                self.logger.error("DQN.set_model_state: Critical: input_shape or output_shape not available after checking state. Cannot build network.")
                self.is_model_built = False # Explicitly set
                return # Cannot proceed without building
        
        # Load weights
        weights_to_load_source = None
        if "model_weights" in state and state["model_weights"] is not None: # Loading from get_model_state output
            weights_to_load_source = state["model_weights"]
            self.logger.info("DQN.set_model_state: Found 'model_weights' in state, will use these for loading.")
        elif isinstance(state, dict) and not any(k in state for k in ["model_weights", "creation_config", "is_model_built"]):
            # Heuristic for raw HPO params (a dict of weights without our specific state keys)
            weights_to_load_source = state
            self.logger.info("DQN.set_model_state: No 'model_weights' or 'creation_config'. Assuming 'state' itself is a flat weight dictionary (raw HPO params).")
        
        if weights_to_load_source and self.q_network and isinstance(self.q_network.get("weights"), dict):
            self.logger.info(f"DQN.set_model_state: Attempting to load weights. Source has {len(weights_to_load_source)} items.")
            target_weights_struct = self.q_network["weights"]
            loaded_weights_count = 0
            
            # Assuming weights_to_load_source is a flat dict {name: array}
            # And target_weights_struct is also {name: array}
            for key_name_target, target_weight_arr_template in target_weights_struct.items():
                if key_name_target in weights_to_load_source:
                    try:
                        source_weight_array = np.array(weights_to_load_source[key_name_target])
                        target_shape = target_weight_arr_template.shape
                        target_dtype = target_weight_arr_template.dtype

                        if target_shape != source_weight_array.shape:
                            self.logger.warning(f"DQN.set_model_state: Shape mismatch for {key_name_target}. Target: {target_shape}, Source: {source_weight_array.shape}. Attempting reshape.")
                            source_weight_array = source_weight_array.reshape(target_shape)
                        
                        target_weights_struct[key_name_target] = source_weight_array.astype(target_dtype)
                        loaded_weights_count += 1
                        self.logger.debug(f"DQN.set_model_state: Loaded weight for {key_name_target}")
                    except Exception as e:
                        self.logger.error(f"DQN.set_model_state: Error loading weight for {key_name_target}: {e}", exc_info=True)
                else:
                    self.logger.warning(f"DQN.set_model_state: Target weight key '{key_name_target}' not found in source weights. Available source keys: {list(weights_to_load_source.keys())}")

            self.logger.info(f"DQN.set_model_state: Loaded {loaded_weights_count} weight arrays into q_network.")
            
            if self.target_network: # Ensure target network is also updated
                self.logger.info("DQN.set_model_state: Updating target_network from q_network.")
                self._update_target_network()
        else:
            self.logger.warning("DQN.set_model_state: No weights_to_load_source identified or q_network structure invalid. Weights not loaded.")
        
        # Restore built status from state if available, otherwise rely on self.build() outcome
        # self.is_model_built should be True if self.build() was successful and q_network exists
        self.is_model_built = state.get("is_model_built", self.q_network is not None)
        
        if not self.q_network: # Final safety check
             self.is_model_built = False
             self.logger.error("DQN.set_model_state: CRITICAL - self.q_network is None at the end of set_model_state.")
        
        self.logger.info(f"DQN.set_model_state completed. Model built status: {self.is_model_built}")
    
    def _calculate_rl_metrics(self, episode_reward: float, episode_length: int,
                             losses: List[float], epsilon: float,
                             episode_portfolio_values: List[float],
                             episode_trades: List[Dict]) -> None:
        """Calculate and store RL-specific metrics for the current episode.
        
        Args:
            episode_reward: Total reward for the episode
            episode_length: Number of steps in the episode
            losses: List of losses from training steps in this episode
            epsilon: Current epsilon value for exploration
            episode_portfolio_values: Portfolio values throughout the episode
            episode_trades: List of trades executed in this episode
        """
        try:
            # 1. RL Episode Reward (same as episode_reward but explicitly named)
            self.training_history["rl_episode_reward"].append(episode_reward)
            
            # 2. RL Episode Length (same as episode_length but explicitly named)
            self.training_history["rl_episode_length"].append(episode_length)
            
            # 3. RL Loss (average loss for this episode)
            if losses:
                avg_loss = float(np.mean(losses))
                self.training_history["rl_loss"].append(avg_loss)
            else:
                self.training_history["rl_loss"].append(np.nan)
            
            # 4. RL Entropy (epsilon-based exploration entropy for DQN)
            # For epsilon-greedy, entropy approximation: -epsilon*log(epsilon) - (1-epsilon)*log(1-epsilon)
            if 0 < epsilon < 1:
                entropy = -(epsilon * np.log(epsilon) + (1 - epsilon) * np.log(1 - epsilon))
            else:
                entropy = 0.0  # No entropy when epsilon is 0 or 1
            self.training_history["rl_entropy"].append(float(entropy))
            
            # 5. RL Learning Rate (current learning rate)
            self.training_history["rl_learning_rate"].append(self.learning_rate)
            
            # 6. RL Value Estimates (average Q-values for this episode)
            # Calculate average Q-value from recent states if available
            if hasattr(self, '_episode_q_values') and self._episode_q_values:
                avg_q_value = float(np.mean(self._episode_q_values))
                self.training_history["rl_value_estimates"].append(avg_q_value)
                # Reset for next episode
                self._episode_q_values = []
            else:
                # Fallback: estimate from current network if we have recent states
                self.training_history["rl_value_estimates"].append(np.nan)
            
            # 7. RL TD Error (temporal difference error - use last loss as proxy)
            if losses:
                # TD error is approximated by the loss (which is based on TD error)
                td_error = float(losses[-1]) if losses else np.nan
                self.training_history["rl_td_error"].append(td_error)
            else:
                self.training_history["rl_td_error"].append(np.nan)
            
            # 8. RL KL Divergence (approximated for DQN using epsilon change)
            # For DQN, we approximate KL divergence by measuring policy change via epsilon
            if len(self.training_history["epsilon_values"]) > 1:
                prev_epsilon = self.training_history["epsilon_values"][-2]
                epsilon_change = abs(epsilon - prev_epsilon)
                # Normalize to [0,1] range as a proxy for KL divergence
                kl_div_approx = min(epsilon_change * 10, 1.0)  # Scale factor for visibility
                self.training_history["rl_kl_divergence"].append(float(kl_div_approx))
            else:
                self.training_history["rl_kl_divergence"].append(0.0)
            
            # 9. RL Explained Variance (variance explained by value function)
            # For DQN, we approximate this using the relationship between predicted and actual returns
            if len(episode_portfolio_values) > 1:
                # Calculate actual returns
                actual_returns = []
                for i in range(1, len(episode_portfolio_values)):
                    if episode_portfolio_values[i-1] > 0:
                        ret = (episode_portfolio_values[i] - episode_portfolio_values[i-1]) / episode_portfolio_values[i-1]
                        actual_returns.append(ret)
                
                if actual_returns:
                    # Use episode reward as predicted return proxy
                    predicted_return = episode_reward / len(actual_returns) if actual_returns else 0
                    actual_var = np.var(actual_returns) if len(actual_returns) > 1 else 0
                    
                    if actual_var > 0:
                        # Simplified explained variance calculation
                        residual_var = np.var([r - predicted_return for r in actual_returns])
                        explained_var = max(0, 1 - (residual_var / actual_var))
                        self.training_history["rl_explained_variance"].append(float(explained_var))
                    else:
                        self.training_history["rl_explained_variance"].append(1.0)  # Perfect prediction if no variance
                else:
                    self.training_history["rl_explained_variance"].append(np.nan)
            else:
                self.training_history["rl_explained_variance"].append(np.nan)
            
            # 10. RL Success Rate (percentage of profitable episodes)
            # Define success as positive episode reward (profitable trading)
            is_successful = episode_reward > 0
            
            # Calculate success rate over recent episodes (last 100 or all if fewer)
            recent_rewards = self.training_history["rl_episode_reward"][-100:]
            if recent_rewards:
                success_count = sum(1 for r in recent_rewards if r > 0)
                success_rate = success_count / len(recent_rewards)
                self.training_history["rl_success_rate"].append(float(success_rate))
            else:
                self.training_history["rl_success_rate"].append(0.0)
            
            # Initialize episode Q-values list for next episode if not exists
            if not hasattr(self, '_episode_q_values'):
                self._episode_q_values = []
                
        except Exception as e:
            self.logger.warning(f"Failed to calculate RL metrics for episode: {e}")
            # Store NaN values for failed calculations
            for metric_name in ["rl_episode_reward", "rl_episode_length", "rl_loss", "rl_entropy",
                               "rl_learning_rate", "rl_value_estimates", "rl_td_error", "rl_kl_divergence",
                               "rl_explained_variance", "rl_success_rate"]:
                if metric_name in self.training_history:
                    if len(self.training_history[metric_name]) == len(self.training_history["episode_rewards"]) - 1:
                        self.training_history[metric_name].append(np.nan)