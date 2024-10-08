from concept_erasure import LeaceFitter, LeaceEraser
import torch as t
import torch.nn.functional as F

class LRProbe(t.nn.Module):
    def __init__(self, d_in):
        super().__init__()
        self.net = t.nn.Sequential(
            t.nn.Linear(d_in, 1, bias=False),
            t.nn.Sigmoid()
        )

    def forward(self, x, iid=None):
        return self.net(x).squeeze(-1)

    def pred(self, x, iid=None):
        return self(x).round()
    
    def forward_tuples(self, xs, iid=False):
        y1 = self(xs[0], iid=iid)
        y2 = self(xs[1], iid=iid)
        pred = y2 - y1 + 0.5 # outputs should be centered around 0.5
        return pred
    
    def from_data(acts, labels, lr=0.001, weight_decay=0.1, epochs=1000, device='cpu'):
        acts, labels = acts.to(device), labels.to(device)
        probe = LRProbe(acts.shape[-1]).to(device)
        
        opt = t.optim.AdamW(probe.parameters(), lr=lr, weight_decay=weight_decay)
        for _ in range(epochs):
            opt.zero_grad()
            loss = t.nn.BCELoss()(probe(acts), labels)
            loss.backward()
            opt.step()
        
        return probe

    @property
    def direction(self):
        return self.net[0].weight.data[0]


class MMProbe(t.nn.Module):
    def __init__(self, direction, covariance=None, inv=None, atol=1e-3):
        super().__init__()
        self.direction = t.nn.Parameter(direction, requires_grad=False)
        if inv is None:
            self.inv = t.nn.Parameter(t.linalg.pinv(covariance, hermitian=True, atol=atol), requires_grad=False)
        else:
            self.inv = t.nn.Parameter(inv, requires_grad=False)

    def forward(self, x, iid=False):
        if iid:
            return t.nn.Sigmoid()(x @ self.inv @ self.direction)
        else:
            return t.nn.Sigmoid()(x @ self.direction)

    def pred(self, x, iid=False):
        return self(x, iid=iid).round()

    def forward_tuples(self, xs, iid=False):
        y1 = self(xs[0], iid=iid)
        y2 = self(xs[1], iid=iid)
        pred = y2 - y1 + 0.5 # outputs should be centered around 0.5
        return pred

    def from_data(acts, labels, atol=1e-3, device='cpu'):
        acts, labels
        pos_acts, neg_acts = acts[labels==1], acts[labels==0]
        pos_mean, neg_mean = pos_acts.mean(0), neg_acts.mean(0)
        direction = pos_mean - neg_mean

        centered_data = t.cat([pos_acts - pos_mean, neg_acts - neg_mean], 0)
        covariance = centered_data.t() @ centered_data / acts.shape[0]
        
        probe = MMProbe(direction, covariance=covariance).to(device)

        return probe    

import torch
from torch import Tensor, nn, optim
from sklearn.metrics import roc_auc_score

class MMProbe_Mallen(nn.Module):
    def __init__(self, in_features: int, device: torch.device, dtype: torch.dtype):
        super().__init__()

        self.linear = nn.Linear(in_features, 1, device=device, dtype=dtype)

        # Learnable Platt scaling parameter
        self.scale = nn.Parameter(torch.ones(1, device=device, dtype=dtype))

    def forward(self, hiddens: Tensor) -> Tensor:
        return self.linear(hiddens).mul(self.scale).squeeze()

    def fit(self, x: Tensor, y: Tensor):
        assert x.ndim == 2, "x must have shape [n, d]"
        diff = x[y == 1].mean(dim=0) - x[y == 0].mean(dim=0)
        diff = diff / diff.norm()

        self.linear.weight.data = diff.unsqueeze(0)

    @torch.no_grad()
    def resolve_sign(self, labels: Tensor, hiddens: Tensor):
        """Flip the scale term if AUROC < 0.5."""
        auroc = roc_auc_score(labels.cpu().numpy(), self.forward(hiddens).cpu().numpy())
        if auroc < 0.5:
            self.scale.data = -self.scale.data

    # My changes to work with GoT code below
    def from_data(acts, labels, atol=1e-3, device='cpu'):
        probe = MMProbe_Mallen(in_features=1, device=device, dtype=torch.float)
        probe.fit(x=acts, y=labels)
        probe.resolve_sign(labels=labels, hiddens=acts)

        return probe
    
    def pred(self, x, iid=False):
        # Ignore iid?
        return self(x) > 0.



def ccs_loss(probe, acts, neg_acts):
    p_pos = probe(acts)
    p_neg = probe(neg_acts)
    consistency_losses = (p_pos - (1 - p_neg)) ** 2
    confidence_losses = t.min(t.stack((p_pos, p_neg), dim=-1), dim=-1).values ** 2
    return t.mean(consistency_losses + confidence_losses)


class CCSProbe(t.nn.Module):
    def __init__(self, d_in):
        super().__init__()
        self.net = t.nn.Sequential(
            t.nn.Linear(d_in, 1, bias=False),
            t.nn.Sigmoid()
        )
    
    def forward(self, x, iid=None):
        return self.net(x).squeeze(-1)
    
    def pred(self, acts, iid=None):
        return self(acts).round()
    
    def from_data(acts, neg_acts, labels=None, lr=0.001, weight_decay=0.1, epochs=1000, device='cpu'):
        acts, neg_acts = acts.to(device), neg_acts.to(device)
        probe = CCSProbe(acts.shape[-1]).to(device)
        
        opt = t.optim.AdamW(probe.parameters(), lr=lr, weight_decay=weight_decay)
        for _ in range(epochs):
            opt.zero_grad()
            loss = ccs_loss(probe, acts, neg_acts)
            loss.backward()
            opt.step()

        if labels is not None: # flip direction if needed
            acc = (probe.pred(acts) == labels).float().mean()
            if acc < 0.5:
                probe.net[0].weight.data *= -1
        
        return probe

    @property
    def direction(self):
        return self.net[0].weight.data[0]
    
class CrcReporter(nn.Module):
    def __init__(self, in_features: int, device: torch.device, dtype: torch.dtype):
        super().__init__()

        self.linear = nn.Linear(in_features, 1, device=device, dtype=dtype)
        self.eraser = None

        # Learnable Platt scaling parameter
        self.scale = nn.Parameter(torch.ones(1, device=device, dtype=dtype))

    def forward(self, hiddens: Tensor, iid = None) -> Tensor:
        return self.raw_forward(hiddens)

    def raw_forward(self, hiddens: Tensor) -> Tensor:
        if self.eraser is not None:
            hiddens = self.eraser(hiddens)
        return self.linear(hiddens).mul(self.scale).squeeze()

    def fit(self, x: Tensor):
        n = len(x)

        self.eraser = LeaceEraser.fit(
            x=x.flatten(0, 1),
            z=t.stack([x.new_zeros(n), x.new_ones(n)], dim=1).flatten(),
        )
        x = self.eraser(x)

        # Top principal component of the contrast pair diffs
        neg, pos = x.unbind(-2)
        _, _, vh = t.pca_lowrank(pos - neg, q=1, niter=10)

        # Use the TPC as the weight vector
        self.linear.weight.data = vh.T

    def platt_scale(self, labels: Tensor, hiddens: Tensor, max_iter: int = 100):
        """Fit the scale and bias terms to data with LBFGS.

        Args:
            labels: Binary labels of shape [batch].
            hiddens: Hidden states of shape [batch, dim].
            max_iter: Maximum number of iterations for LBFGS.
        """
        _, k, _ = hiddens.shape
        labels = F.one_hot(labels.long(), k)

        opt = optim.LBFGS(
            [self.linear.bias, self.scale],
            line_search_fn="strong_wolfe",
            max_iter=max_iter,
            tolerance_change=t.finfo(hiddens.dtype).eps,
            tolerance_grad=t.finfo(hiddens.dtype).eps,
        )

        def closure():
            opt.zero_grad()
            loss = nn.functional.binary_cross_entropy_with_logits(
                self.raw_forward(hiddens), labels.float()
            )
            loss.backward()
            return float(loss)

        opt.step(closure)

    def from_data(acts, neg_acts, labels=None, atol=1e-3, device='cpu'):
        probe = CrcReporter(in_features=1, device=device, dtype=t.float)
        x = t.stack([acts, neg_acts], dim=1)
        probe.fit(x=x)
        probe.platt_scale(labels=labels, hiddens=x)

        return probe
    
    def pred(self, x, iid=False):
        # Ignore iid?
        return self(x) > 0.
