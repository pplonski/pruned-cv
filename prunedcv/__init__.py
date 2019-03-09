from sklearn.model_selection import KFold, ParameterGrid
from sklearn import metrics
import numpy
import pandas


class PrunedGridSearchCV:

    def __init__(self,
                 estimator,
                 params_grid,
                 cv,
                 tolerance,
                 splits_to_start_pruning=2,
                 minimize=True,
                 probabilistic_prun=False):

        self.estimator = estimator
        self.params_grid = params_grid
        self.cv = cv
        self.tolerance = tolerance
        self.splits_to_start_pruning = splits_to_start_pruning
        self.minimize = minimize
        self.probabilistic_prun=probabilistic_prun
        self.params_grid_iterable = ParameterGrid(self.params_grid)
        self.best_params = None
        self.best_score = None

    def fit(self,
            x,
            y,
            metric='mse',
            shuffle=False,
            random_state=None):

        pruner = PrunedCV(self.cv,
                          self.tolerance,
                          self.splits_to_start_pruning,
                          self.minimize,
                          self.probabilistic_prun)

        for params_set in self.params_grid_iterable:
            self.estimator.set_params(**params_set)
            score = pruner.cross_val_score(self.estimator,
                                           x,
                                           y,
                                           metric=metric,
                                           shuffle=shuffle,
                                           random_state=random_state)

            if self.best_score is not None:
                if (self.minimize and self.best_score > score) or (not self.minimize and self.best_score < score):
                    self.best_score = score
                    self.best_params = params_set
            else:
                self.best_score = score
                self.best_params = params_set


class PrunedCV:

    def __init__(self,
                 cv,
                 tolerance,
                 splits_to_start_pruning=2,
                 minimize=True,
                 probabilistic_prun=False):

        if not isinstance(cv, int):
            raise TypeError
        if cv < 2:
            raise ValueError

        self.cv = cv
        self.set_tolerance(tolerance)
        self.splits_to_start_pruning = splits_to_start_pruning
        self.minimize = minimize
        self.probabilistic_prun = probabilistic_prun
        self.prun = False
        self.cross_val_score_value = None
        self.current_splits_list_ = []
        self.best_splits_list_ = []
        self.first_run_ = True
        
    def set_tolerance(self,
                      tolerance):

        if not isinstance(tolerance, float):
            raise TypeError
        if tolerance < 0:
            raise ValueError

        self.tolerance = tolerance

    def cross_val_score(self,
                        model,
                        x,
                        y,
                        metric='mse',
                        shuffle=False,
                        random_state=None):

        if not isinstance(x, (numpy.ndarray, pandas.core.frame.DataFrame)):
            raise TypeError

        if not isinstance(y, (numpy.ndarray, pandas.core.series.Series)):
            raise TypeError

        if metric not in ['mse',
                          'mae']:
            raise ValueError

        kf = KFold(n_splits=self.cv,
                   shuffle=shuffle,
                   random_state=random_state)
        for train_idx, test_idx in kf.split(x, y):
            if not self.prun:

                if isinstance(x, numpy.ndarray):
                    x_train = x[train_idx]
                    x_test = x[test_idx]
                else:
                    x_train = x.iloc[train_idx, :]
                    x_test = x.iloc[test_idx, :]
                if isinstance(y, numpy.ndarray):
                    y_train = y[train_idx]
                    y_test = y[test_idx]
                else:
                    y_train = y.iloc[train_idx]
                    y_test = y.iloc[test_idx]

                model.fit(x_train, y_train)
                y_test_teor = model.predict(x_test)

                if metric == 'mse':
                    self.add_split_value_and_prun(metrics.mean_squared_error(y_test, y_test_teor))
                elif metric == 'mae':
                    self.add_split_value_and_prun(metrics.mean_absolute_error(y_test, y_test_teor))

        self.prun = False
        return self.cross_val_score_value

    def add_split_value_and_prun(self, value):

        if not isinstance(value, float):
            raise TypeError

        if len(self.current_splits_list_) == 0:
            self.prun = False

        if self.minimize:
            self.current_splits_list_.append(value)
        else:
            self.current_splits_list_.append(-value)

        if self.first_run_:
            self._populate_best_splits_list_at_first_run(value)
        else:
            self._decide_prun()

        if len(self.current_splits_list_) == self.cv:
            self._serve_last_split()

    def _populate_best_splits_list_at_first_run(self, value):

        self.best_splits_list_.append(value)

        if len(self.best_splits_list_) == self.cv:
            self.first_run_ = False

    def _decide_prun(self):

        split_num = len(self.current_splits_list_)
        mean_best_splits = sum(self.best_splits_list_[:split_num]) / split_num
        mean_curr_splits = sum(self.current_splits_list_) / split_num

        if self.cv > split_num >= self.splits_to_start_pruning:

            if self.probabilistic_prun:
                self.prun = self._probabilistic_prun_decision(split_num,
                                                              mean_best_splits,
                                                              mean_curr_splits)

            else:
                self.prun = self._significantly_higher_value(mean_best_splits,
                                                             mean_curr_splits,
                                                             self.minimize,
                                                             self.tolerance)

            if self.prun:
                self.cross_val_score_value = self._predict_pruned_score(mean_curr_splits,
                                                                        mean_best_splits)
                self.current_splits_list_ = []

    def _probabilistic_prun_decision(self, split_num,
                                     mean_best_splits,
                                     mean_curr_splits):

        if self.minimize and mean_curr_splits < mean_best_splits:
            return False

        elif not self.minimize and mean_curr_splits > mean_best_splits:
            return False

        else:
            alpha = sum([i[0] < i[1] for i in zip(self.best_splits_list_[:split_num],
                                                  self.current_splits_list_)])

            beta = sum([i[0] > i[1] for i in zip(self.best_splits_list_[:split_num],
                                                 self.current_splits_list_)])

            random_value = numpy.random.beta(1 + alpha, 1 + beta)

            if self.minimize:
                return random_value > 0.5
            else:
                return random_value < 0.5

    @staticmethod
    def _significantly_higher_value(mean_best_splits,
                                    mean_curr_splits,
                                    minimize,
                                    tolerance):
        tolerance_scaler_if_min = 1 + minimize * tolerance
        tolerance_scaler_if_max = 1 + (1 - minimize) * tolerance
        return mean_best_splits * tolerance_scaler_if_min < mean_curr_splits * tolerance_scaler_if_max

    def _predict_pruned_score(self,
                              mean_curr_splits,
                              mean_best_splits):
        return (mean_curr_splits / mean_best_splits) * (sum(self.best_splits_list_) / self.cv)

    def _serve_last_split(self):

        if sum(self.best_splits_list_) > sum(self.current_splits_list_):
            self.best_splits_list_ = self.current_splits_list_

        self.cross_val_score_value = sum(self.current_splits_list_) / self.cv
        self.current_splits_list_ = []
