import pytorch_lightning as pl
import argparse
from collections import OrderedDict, deque
from typing import Tuple, List
import torch.optim as optim
from torch.optim import Optimizer
from torch.utils.data import DataLoader


from rezero.agent import BasicAgent
from rezero.memory import ReplayBuffer, SimpleRLDataset
from rezero.net import DQN



class DQNLightning(pl.LightningModule):
    """ Basic DQN Model """

    def __init__(self, hparams: argparse.Namespace) -> None:
        super().__init__()
        self.hparams = hparams

        self.env = gym.make(self.hparams.env)
        obs_size = self.env.observation_space.shape[0]
        n_actions = self.env.action_space.n

        self.net = DQN(obs_size, n_actions)
        self.target_net = DQN(obs_size, n_actions)

        self.buffer = ReplayBuffer(self.hparams.replay_size)
        self.agent = Agent(self.env, self.buffer)
        self.total_reward = 0
        self.episode_reward = 0
        self.populate(self.hparams.warm_start_steps)


    def populate(self, steps: int = 1000) -> None:
        """
        Carries out several random steps through the environment to initially fill
        up the replay buffer with experiences

        Args:
            steps: number of random steps to populate the buffer with
        """
        for i in range(steps):
            self.agent.play_step(self.net, epsilon=1.0)


    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        Passes in a state x through the network and gets the q_values of each action as an output

        Args:
            x: environment state

        Returns:
            q values
        """
        output = self.net(x)
        return output


    def dqn_mse_loss(self, batch: Tuple[torch.Tensor, torch.Tensor]) -> torch.Tensor:
        """
        Calculates the mse loss using a mini batch from the replay buffer

        Args:
            batch: current mini batch of replay data

        Returns:
            loss
        """
        states, actions, rewards, dones, next_states = batch

        state_action_values = self.net(states).gather(1, actions.unsqueeze(-1)).squeeze(-1)

        with torch.no_grad():
            next_state_values = self.target_net(next_states).max(1)[0]
            next_state_values[dones] = 0.0
            next_state_values = next_state_values.detach()

        expected_state_action_values = next_state_values * self.hparams.gamma + rewards

        return nn.MSELoss()(state_action_values, expected_state_action_values)


    def training_step(self, batch: Tuple[torch.Tensor, torch.Tensor], nb_batch) -> OrderedDict:
        """
        Carries out a single step through the environment to update the replay buffer.
        Then calculates loss based on the minibatch recieved

        Args:
            batch: current mini batch of replay data
            nb_batch: batch number

        Returns:
            Training loss and log metrics
        """
        device = self.get_device(batch)
        epsilon = max(self.hparams.eps_end, self.hparams.eps_start -
                      self.global_step + 1 / self.hparams.eps_last_frame)

        # step through environment with agent
        reward, done = self.agent.play_step(self.net, epsilon, device)
        self.episode_reward += reward

        # calculates training loss
        loss = self.dqn_mse_loss(batch)

        if self.trainer.use_dp or self.trainer.use_ddp2:
            loss = loss.unsqueeze(0)

        if done:
            self.total_reward = self.episode_reward
            self.episode_reward = 0

        # Soft update of target network
        if self.global_step % self.hparams.sync_rate == 0:
            self.target_net.load_state_dict(self.net.state_dict())

        log = {'total_reward': torch.tensor(self.total_reward).to(device),
               'reward': torch.tensor(reward).to(device),
               'train_loss': loss
               }
        status = {'steps': torch.tensor(self.global_step).to(device),
                  'total_reward': torch.tensor(self.total_reward).to(device)
                  }

        

        return OrderedDict({'loss': loss, 'log': log, 'progress_bar': status})


    def configure_optimizers(self) -> List[Optimizer]:
        """ Initialize Adam optimizer"""
        optimizer = optim.Adam(self.net.parameters(), lr=self.hparams.lr)
        return [optimizer]


    def __dataloader(self) -> DataLoader:
        """Initialize the Replay Buffer dataset used for retrieving experiences"""
        dataset = RLDataset(self.buffer, self.hparams.episode_length)
        dataloader = DataLoader(dataset=dataset, batch_size=self.hparams.batch_size)
        return dataloader


    def train_dataloader(self) -> DataLoader:
        """Get train loader"""
        return self.__dataloader()


    def get_device(self, batch) -> str:
        """Retrieve device currently being used by minibatch"""
        return batch[0].device.index if self.on_gpu else 'cpu'



#---------------------------------------------------------------------------------
# For testing
#---------------------------------------------------------------------------------

# import numpy as np
# import argparse


# def main(hparams) -> None:
#     model = DQNLightning(hparams)

#     trainer = pl.Trainer(
#         gpus=1,
#         distributed_backend='dp',
#         max_epochs=500,
#         early_stop_callback=False,
#         val_check_interval=100
#     )

#     trainer.fit(model)


# torch.manual_seed(0)
# np.random.seed(0)

# parser = argparse.ArgumentParser()
# parser.add_argument("--batch_size", type=int, default=16, help="size of the batches")
# parser.add_argument("--lr", type=float, default=1e-2, help="learning rate")
# parser.add_argument("--env", type=str, default="CartPole-v0", help="gym environment tag")
# parser.add_argument("--gamma", type=float, default=0.99, help="discount factor")
# parser.add_argument("--sync_rate", type=int, default=10,
#                     help="how many frames do we update the target network")
# parser.add_argument("--replay_size", type=int, default=1000,
#                     help="capacity of the replay buffer")
# parser.add_argument("--warm_start_size", type=int, default=1000,
#                     help="how many samples do we use to fill our buffer at the start of training")
# parser.add_argument("--eps_last_frame", type=int, default=1000,
#                     help="what frame should epsilon stop decaying")
# parser.add_argument("--eps_start", type=float, default=1.0, help="starting value of epsilon")
# parser.add_argument("--eps_end", type=float, default=0.01, help="final value of epsilon")
# parser.add_argument("--episode_length", type=int, default=200, help="max length of an episode")
# parser.add_argument("--max_episode_reward", type=int, default=200,
#                     help="max episode reward in the environment")
# parser.add_argument("--warm_start_steps", type=int, default=1000,
#                     help="max episode reward in the environment")

# args, _ = parser.parse_known_args()

# main(args)
