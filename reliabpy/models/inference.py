import numpy as np
from scipy.stats import norm
from reliabpy.models.observation import Probability_of_Detection as PoD

import torch

class _Base(object):
    def _global_init(self):
        self.store_results = True
        self.t, self.action, self.output = 0, None, None

        self.force_detection =  False
        self.force_notdetection = False


    def _store_results(self):
        if self.store_results:
            self.results.append([
                self.t, 
                self.pf,
                self.action,
                self.output
                ])

    def get_pf(self):
        """
        Get P_f
        =======

        Get the probability of failure and its timestep.
        Needs model with <store_results = True>.

        Return:
        -------
        time : array
            time for the pf
        pf : array
            probability of failure
        """
        # Get the probability of failure. Needs <store_results = True>
        if not self.store_results:
            raise Warning("No stored probability of failure: store_results is", self.store_results)
        
        results = np.array(self.results)
        time, pf = results[:,0], results[:,1]

        return time, pf
    
    def get_results(self):
        """
        Get results
        ===========
        
        Return a dictionary with the results for the entire episode: 
        - year   : year
        - pf     : probability of failure
        - action    : observation
        - output : output on a component

        Return:
        -------
        results : dict
            year, pf, action, output
        """

        if not self.store_results:
            raise Warning("No stored probability of failure: store_results is", self.store_results)

        results = np.array(self.results)

        return {"year" : results[:,0], "pf" : results[:,1], "action" : results[:,2], "output" : results[:,3]}

class MonteCarloSimulation(_Base):
    """
    Monte Carlo Simulation
    ======================

    Parameters:
    -----------
    a_0 : array
        initial crack size of the samples
    function : function
        funtion to propagate over time with <a> only as an input 
    a_crit : float
        critical crack depth
    """
    def __init__(self, a_0, function, a_crit):
        self._global_init()

        self.a_0 = a_0 
        self.a = a_0.copy()
        self.f = function
        self.a_crit = a_crit

        self.num_samples = len(self.a_0)

        self.pf = self.get_prob_fail()

        self.t = 0
        self.PoD = np.ones_like(a_0)

        if self.store_results:
            self.results = [[
                self.t, 
                self.pf,
                None,
                None]]

    def predict(self):
        """
        Predict
        =======
        
        Propagate one time step.
        """
        self.a = self.f()
        self.t += 1

        if self.store_results: self._store_results()

    def update(self, parameters):
        """
        Update
        ======

        Update the current state with (so far) Probability of Detection inspection model.

        TODO: check this restriction for only PoD

        Parameters:
        -----------
        parameters : dict
            inspection parameters. so far, with only "quality" key (good, normal and bad)
        """
        self.action = 'PoD'
        parameters, invPoD_func = PoD.get_settings(parameters['quality'], inverse=True)

        uniform_dist = np.random.uniform(size=self.num_samples)
        a_detected = invPoD_func(uniform_dist, **parameters)
        samples_detected = self.a > a_detected
        self.crack_detected = bool(np.random.binomial(1, samples_detected.sum()/self.num_samples))
        if any([self.force_detection, self.force_notdetection]):
            if self.force_detection:
                gH = samples_detected
            if self.force_notdetection:
                gH = self.a <= a_detected
        else:
            if self.crack_detected:
                gH = samples_detected
            else:
                gH = self.a <= a_detected
        self.PoD = self.PoD*gH

        if self.store_results: self._store_results()

    def get_prob_fail(self):
        """
        Get probability of failure
        ==========================

        Get the probability of failure for the current timestep

        Returns
        -------
        pf : float
            current probability of failure
        """
        failed_samples = self.a > self.a_crit

        total = self.PoD.sum()
        failed_detected = np.sum(failed_samples*self.PoD)

        return failed_detected/total

class TransitionMatrix:
    # TODO: Transition matrix class
    """
    Transtion Matrix
    ================
    
    Build the transtion matrix for a given function and discretization scheme.

    Parameters:
    -----------
    discretization : array
        initial state vector (& x n)
    function : function
        propagation function over time: f(a)
    """
    def __init__(self, discretization, function):
        self.discretization = discretization
        self.function = function

    def build_T(self):
        """
        Build T
        =======

        Build transtiion matrix.

        Returns:
        --------
        T : matrix
            transtion matrix
        """
        T = None
        return T

class DynamicBayesianNetwork(_Base):
    '''
    Component level dynamic Bayesian network
    ========================================

    Dynamic Bayesian nework for one component.

    Parameters:
    -----------
    T : matrix
        (n x n) transtiion matrix
    s0 : array
        initial state vector (1 x n)
    discretization : dict
        discretization of all variables (e.g.: crack depth and time) 
    '''
    
    def __init__(self, T, s0, discretizations):
        self._global_init()

        self.T = torch.Tensor(T)
        self.s = torch.Tensor(s0)
        self.s0 = torch.Tensor(s0.copy())
        self.discretizations = discretizations

        self.states_values = np.diff(discretizations['a']/2) + discretizations['a'][:-1]

        self.n_states = {}
        self.total_nstates = 1
        for var_name in discretizations:
            quant = int(len(discretizations[var_name]) - 1)
            self.n_states[var_name] = quant
            self.total_nstates *= quant
        
        self.pf = self.get_prob_fail()

        if self.store_results:
            self.results = [[
                self.t, 
                self.pf,
                self.action,
                self.output]]

    def predict(self):
        """
        Predict
        =======
        
        Propagate one time step.
        """
        
        self.s = torch.matmul(self.s, self.T)
        
        self.t += 1
        self.pf = self.get_prob_fail()
        self.action = None
        self.output = None
        
        if self.store_results: self._store_results()
    
    def update(self, insp_quality):
        """
        Update
        ======

        Update the current state with (so far) Probability of Detection inspection model.

        TODO: check this restriction for only PoD

        Parameters:
        -----------
        parameters : dict
            inspection parameters. so far, with only "quality" key (good, normal and bad)
        """

        parameters, function = PoD.get_settings(insp_quality)
        obs_pmf = function(self.states_values, **parameters)

        obs_state = np.tile(obs_pmf, int(self.total_nstates/len(obs_pmf)))
        obs_state = torch.Tensor(obs_state)
        detected_pmf = torch.mul(self.s, obs_state)

        self.crack_detected = bool(np.random.binomial(1, detected_pmf.sum()))
        if any([self.force_detection, self.force_notdetection]):
            if self.force_detection:
                self.s = detected_pmf
            if self.force_notdetection:
                self.s = self.s*(1 - obs_state)
        else:
            if self.crack_detected:
                self.s = detected_pmf
            else:
                self.s = self.s*(1 - obs_state)

        self.s /= self.s.sum()
        self.pf = self.get_prob_fail()

        self.t += 1e-6 # to enable Dataframe Concat
        self.pf = self.get_prob_fail()
        self.action = 'PoD'
        if self.crack_detected: self.output = 'D'
        
        if self.store_results: self._store_results()
    
    def perform_action(self):
        """
        Perform output
        ==============

        Perform perfect repair output.
        """
        self.s = self.s0

        self.t += 1e-6 # to enable Dataframe Concat
        self.pf = self.get_prob_fail()
        self.action = 'PR'
        self.output = 's0'

        if self.store_results: self._store_results()
        
    def get_prob_fail(self):
        """
        Get probability of failure
        ==========================

        Get the probability of failure for the current timestep

        Returns
        -------
        pf : float
            current probability of failure
        """
        s = self._reorder()
        return s.sum(axis=0)[-1]

    def _reorder(self):
        return self.s.reshape(list(self.n_states.values()))
    
    def _discretize(discretization, dist_params, function):
        dist_params['a'] = discretization
        return np.diff(function(**dist_params))/np.diff(discretization)

class metrics:
    """
    Metrics
    =======
    
    Metric functions for performance comparisons.
    """
    def pf_rmse(model_1, model_2):
        """
        RMSE for P_f
        ============

        Root mean square error for the probability of failure of two models

        Parameters:
        -----------
        model_1 : inference model
            inference model
        model_2 : inference model
            inference model

        Returns:
        --------
        rmse : float
            Root mean square
        """
        pf1 = model_1.get_pf()[1]
        pf2 = model_2.get_pf()[1]

        return np.sqrt(((pf1 - pf2)**2.0).mean())