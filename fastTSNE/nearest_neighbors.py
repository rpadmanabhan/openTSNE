import sys

import numpy as np
from sklearn import neighbors
from sklearn.utils import check_random_state
from fastTSNE.vptree import VPTree as c_vptree

# In case we're running on a 32bit system, we have to properly handle numba's
# ``parallel`` directive, which throws a ``RuntimeError``. It is important to
# patch this before importing ``pynndescent`` which heavily relies on numba
uns1 = sys.platform.startswith("win32") and sys.version_info[:2] == (2, 7)
uns2 = sys.maxsize <= 2 ** 32
if uns1 or uns2:
    import numba

    __njit_copy = numba.njit

    # Ignore njit decorator and run raw Python function
    def __njit_wrapper(*args, **kwargs):
        return lambda f: f

    numba.njit = __njit_wrapper

    from . import pynndescent

    pynndescent.pynndescent_.numba.njit = __njit_wrapper
    pynndescent.distances.numba.njit = __njit_wrapper
    pynndescent.rp_trees.numba.njit = __njit_wrapper
    pynndescent.utils.numba.njit = __njit_wrapper

from . import pynndescent

# To keep things simple and consistent, we'll only support distances that are
# included in both exact and approximation nearest neighbor search libraries
__vptree_metrics = set(c_vptree.valid_metrics)
__nndescent_metrics = set(pynndescent.distances.named_distances)
VALID_METRICS = sorted(list(__vptree_metrics & __nndescent_metrics))


class KNNIndex:
    def __init__(self, metric, metric_params=None, n_jobs=1, random_state=None):
        self.index = None
        self.metric = metric
        self.metric_params = metric_params
        self.n_jobs = n_jobs
        self.random_state = random_state

    def build(self, data):
        """Build the index so we can query nearest neighbors."""

    def query_train(self, data, k):
        """Query the index for the points used to build index."""

    def query(self, query, k):
        """Query the index with new points."""


class BallTree(KNNIndex):
    def build(self, data):
        self.index = neighbors.NearestNeighbors(
            algorithm="ball_tree", metric=self.metric,
            metric_params=self.metric_params, n_jobs=self.n_jobs,
        )
        self.index.fit(data)

    def query_train(self, data, k):
        distances, neighbors = self.index.kneighbors(n_neighbors=k + 1)
        return neighbors, distances

    def query(self, query, k):
        distances, neighbors = self.index.kneighbors(query, n_neighbors=k)
        return neighbors, distances


class VPTree(KNNIndex):
    def build(self, data):
        data = np.ascontiguousarray(data, dtype=np.float64)
        self.index = c_vptree(data)

    def query_train(self, data, k):
        indices, distances = self.index.query_train(k + 1, num_threads=self.n_jobs)
        return indices[:, 1:], distances[:, 1:]

    def query(self, query, k):
        query = np.ascontiguousarray(query, dtype=np.float64)
        return self.index.query(query, k, num_threads=self.n_jobs)


class NNDescent(KNNIndex):
    def build(self, data):
        random_state = check_random_state(self.random_state)

        # These values were taken from UMAP, which we assume to be sensible defaults
        n_trees = 5 + int(round((data.shape[0]) ** 0.5 / 20))
        n_iters = max(5, int(round(np.log2(data.shape[0]))))

        self.index = pynndescent.NNDescent(
            data, metric=self.metric, metric_kwds=self.metric_params,
            random_state=random_state, n_trees=n_trees, n_iters=n_iters,
            algorithm="standard", max_candidates=60,
        )

    def query_train(self, data, k):
        search_neighbors = min(data.shape[0] - 1, k + 1)
        neighbors, distances = self.index.query(data, k=search_neighbors, queue_size=1)
        return neighbors[:, 1:], distances[:, 1:]

    def query(self, query, k):
        return self.index.query(query, k=k, queue_size=1)
