# -*- coding: utf-8 -*-
"""
Created on Thu Oct 13 17:29:27 2011

@author: Ashley Milsted

TODO:
    - Implement evaluation of the error due to restriction to bond dim.
    - Investigate whether a different gauge choice would reduce numerical inaccuracies.
        - The current choice gives us r_n = eye() and l_n containing
          the Schmidt spectrum.
        - Maybe the l's could be better conditioned?
    - Build more into TakeStep or add a new method that does Restore_ON_R etc. itself.
    - Add an algorithm for expanding the bond dimension.
    - Adaptive step size.

"""
import scipy as sp
import scipy.linalg as la
from scipy import *
import nullspace as ns
from matmul import *

class evoMPS_TDVP_Generic:
    odr = 'C'
    typ = complex128
    
    #Numsites
    N = 0
    
    D = None
    q = None
    
    h_nn = None
    h_ext = None
    
    eps = 0
    
    def SetupAs(self):
        """Initializes the state to full rank with norm 1.
        """
        for n in xrange(1, self.N + 1):
            self.A[n].fill(0)
            
            f = sqrt(1. / self.q[n])
            
            if self.D[n-1] == self.D[n]:
                for s in xrange(self.q[n]):
                    fill_diagonal(self.A[n][s], f)
            else:
                x = 0
                y = 0
                s = 0
                
                if self.D[n] > self.D[n - 1]:
                    f = 1.
                
                for i in xrange(max((self.D[n], self.D[n - 1]))):
                    self.A[n][s, x, y] = f
                    x += 1
                    y += 1
                    if x >= self.A[n][s].shape[0]:
                        x = 0
                        s += 1
                    elif y >= self.A[n][s].shape[1]:
                        y = 0
                        s += 1
    
    def Randomize(self):
        """Set A's randomly, trying to keep the norm reasonable.
        
        We need to ensure that the M matrix in EpsR() is positive definite. How?
        Does it really have to be?
        """
        for n in xrange(1, self.N + 1):
            self.A[n].real = (rand(self.D[n - 1], self.D[n]) - 0.5) / sqrt(self.q[n]) #/ sqrt(self.N) #/ sqrt(self.D[n])
            self.A[n].imag = (rand(self.D[n - 1], self.D[n]) - 0.5) / sqrt(self.q[n]) #/ sqrt(self.N) #/ sqrt(self.D[n])
                
        self.Restore_ON_R()
            
    def __init__(self, numsites, D, q):
        """Creates a new TDVP_MPS object.
        
        The TDVP_MPS class implements the time-dependent variational principle 
        for matrix product states for systems with open boundary conditions and
        a hamiltonian consisting of a nearest-neighbour interaction term and a 
        single-site term (external field).
        
        Bond dimensions will be adjusted where they are too high to be useful.
        FIXME: Add reference.
        
        Parameters
        ----------
        numsites : int
            The number of lattice sites.
        D : ndarray
            A 1-d array, length numsites, of integers indicating the desired bond dimensions.
        q : ndarray
            A 1-d array, also length numsites, of integers indicating the 
            dimension of the hilbert space for each site.
    
        Returns
        -------
        sqrt_A : ndarray
            An array of the same shape and type as A containing the matrix square root of A.        
        """
        self.eps = finfo(self.typ).eps
        
        self.N = numsites
        self.D = array(D)
        self.q = array(q)
        
        #Make indicies correspond to the thesis
        self.K = empty((self.N + 1), dtype=ndarray) #Elements 1..N
        self.C = empty((self.N), dtype=ndarray) #Elements 1..N-1
        self.A = empty((self.N + 1), dtype=ndarray) #Elements 1..N
        
        self.r = empty((self.N + 1), dtype=ndarray) #Elements 0..N
        self.l = empty((self.N + 1), dtype=ndarray)        
        
        if (self.D.ndim != 1) or (self.q.ndim != 1):
            raise NameError('D and q must be 1-dimensional!')
            
        #TODO: Check for integer type.
        
        #Don't do anything pointless
        self.D[0] = 1
        self.D[self.N] = 1
        
        qacc = 1
        for n in reversed(xrange(self.N)):
            qacc *= self.q[n + 1]
            if self.D[n] > qacc:
                self.D[n] = qacc
                
        qacc = 1
        for n in xrange(1, self.N + 1):
            qacc *= q[n - 1]
            if self.D[n] > qacc:
                self.D[n] = qacc
        
        matmul_init(dtype=self.typ, order=self.odr)
        
        self.r[0] = zeros((self.D[0], self.D[0]), dtype=self.typ, order=self.odr)  
        self.l[0] = eye(self.D[0], self.D[0], dtype=self.typ).copy(order=self.odr) #Already set the 0th element (not a dummy)    
    
        for n in xrange(1, self.N + 1):
            self.K[n] = zeros((self.D[n-1], self.D[n-1]), dtype=self.typ, order=self.odr)    
            self.r[n] = zeros((self.D[n], self.D[n]), dtype=self.typ, order=self.odr)
            self.l[n] = zeros((self.D[n], self.D[n]), dtype=self.typ, order=self.odr)
            self.A[n] = empty((self.q[n], self.D[n-1], self.D[n]), dtype=self.typ, order=self.odr)
            if n < self.N:
                self.C[n] = empty((self.q[n], self.q[n+1], self.D[n-1], self.D[n+1]), dtype=self.typ, order=self.odr)
        fill_diagonal(self.r[self.N], 1.)
        self.SetupAs()
    
    def BuildC(self, n_low=-1, n_high=-1):
        """Generates the C matrices used to calculate the K's and ultimately the B's
        
        These are to be used on one side of the super-operator when applying the
        nearest-neighbour Hamiltonian, similarly to C in eqn. (44) of 
        arXiv:1103.0936v2 [cond-mat.str-el], except being for the non-norm-preserving case.

        Makes use only of the nearest-neighbour hamiltonian, and of the A's.
        
        C[n] depends on A[n] and A[n + 1].
        
        """
        if n_low < 1:
            n_low = 1
        if n_high < 1:
            n_high = self.N
        
        for n in xrange(n_low, n_high):
            self.C[n].fill(0)
            AA = empty_like(self.C[n][0][0], order='A')
            for u in xrange(self.q[n]):
                for v in xrange(self.q[n + 1]):
                    matmul(AA, self.A[n][u], self.A[n + 1][v]) #only do this once for each 
                    for s in xrange(self.q[n]):
                        for t in xrange(self.q[n + 1]):                
                            h_nn_stuv = self.h_nn(n, s, t, u, v)
                            if h_nn_stuv != 0:
                                self.C[n][s, t] += h_nn_stuv * AA
    
    def CalcK(self, n_low=-1, n_high=-1):
        """Generates the K matrices used to calculate the B's
        
        K[n] is recursively defined. It depends on C[m] and A[m] for all m >= n.
        
        It directly depends on A[n], A[n + 1], r[n], r[n + 1], C[n] and K[n + 1].
        
        This is equivalent to K on p. 14 of arXiv:1103.0936v2 [cond-mat.str-el], except 
        that it is for the non-gauge-preserving case, and includes a single-site
        Hamiltonian term.
        
        K[1] is, assuming a normalized state, the expectation value H of Ĥ.
        
        Instead of an explicit single-site term here, one could also include the 
        single-site Hamiltonian in the nearest-neighbour term, which may be more 
        efficient.
        """
        if n_low < 1:
            n_low = 1
        if n_high < 1:
            n_high = self.N + 1
            
        for n in reversed(xrange(n_low, n_high)):
            self.K[n].fill(0)
            tmp = empty_like(self.K[n])
            if n < self.N:
                for s in xrange(self.q[n]):
                    for t in xrange(self.q[n+1]):
                        self.K[n] += matmul(tmp, self.C[n][s, t], self.r[n + 1], H(self.A[n+1][t]), H(self.A[n][s]))
                    self.K[n] += matmul(tmp, self.A[n][s], self.K[n + 1], H(self.A[n][s]))
                    
            for s in xrange(self.q[n]):
                for t in xrange(self.q[n]):
                    h_ext_st = self.h_ext(n, s, t)
                    if h_ext_st != 0:
                        self.K[n] += h_ext_st * matmul(tmp, self.A[n][t], self.r[n], H(self.A[n][s]))
    
    def BuildVsh(self, n, sqrt_r):
        """Generates H(V[n][s]) for a given n, used for generating B[n][s]
        
        This is described on p. 14 of arXiv:1103.0936v2 [cond-mat.str-el] for left 
        gauge fixing. Here, we are using right gauge fixing.
        
        Array slicing and reshaping is used to manipulate the indices as necessary.
        
        Each V[n] directly depends only on A[n] and r[n].
        
        We return the conjugate H(V) because we use it in more places than V.
        """
        R = zeros((self.D[n], self.q[n], self.D[n-1]), dtype=self.typ, order='C')
        
        for s in xrange(self.q[n]):
            R[:,s,:] = matmul(None, sqrt_r, H(self.A[n][s]))

        R = R.reshape((self.q[n] * self.D[n], self.D[n-1]))
        V = H(ns.nullspace(H(R)))
        #print (q[n]*D[n] - D[n-1], q[n]*D[n])
        #print V.shape
        #print allclose(mat(V) * mat(V).H, eye(q[n]*D[n] - D[n-1]))
        #print allclose(mat(V) * mat(Rh).H, 0)
        V = V.reshape((self.q[n] * self.D[n] - self.D[n - 1], self.D[n], self.q[n])) #this works with the above form for R
        
        #prepare for using V[s] and already take the adjoint, since we use it more often
        Vsh = empty((self.q[n], self.D[n], self.q[n] * self.D[n] - self.D[n - 1]), dtype=self.typ, order=self.odr)
        for s in xrange(self.q[n]):
            Vsh[s] = H(V[:,:,s])
        
        return Vsh
        
    def CalcOpt_x(self, n, Vsh, sqrt_l, sqrt_r, sqrt_l_inv, sqrt_r_inv):
        """Calculate the parameter matrix x* giving the desired B.
        
        This is equivalent to eqn. (49) of arXiv:1103.0936v2 [cond-mat.str-el] except 
        that, here, norm-preservation is not enforced, such that the optimal 
        parameter matrices x*_n (for the parametrization of B) are given by the 
        derivative w.r.t. x_n of <Phi[B, A]|Ĥ|Psi[A]>, rather than 
        <Phi[B, A]|Ĥ - H|Psi[A]> (with H = <Psi|Ĥ|Psi>).
        
        An additional sum was added for the single-site hamiltonian.
        
        Some multiplications have been pulled outside of the sums for efficiency.
        
        Direct dependencies: 
            - A[n - 1], A[n], A[n + 1]
            - r[n], r[n + 1], l[n - 2], l[n - 1]
            - C[n], C[n - 1]
            - K[n + 1]
            - V[n]
        """
        x = zeros((self.D[n - 1], self.q[n] * self.D[n] - self.D[n - 1]), dtype=self.typ, order=self.odr)
        x_part = empty_like(x)
        x_subpart = empty_like(self.A[n][0])
        x_subsubpart = empty_like(self.A[n][0])
        tmp = empty_like(x_subpart)
        
        x_part.fill(0)
        for s in xrange(self.q[n]):
            x_subpart.fill(0)    
            
            if n < self.N:
                x_subsubpart.fill(0)
                for t in xrange(self.q[n + 1]):
                    x_subsubpart += matmul(tmp, self.C[n][s,t], self.r[n + 1], H(self.A[n + 1][t])) #~1st line
                    
                x_subsubpart += matmul(tmp, self.A[n][s], self.K[n + 1]) #~3rd line               
                
                x_subpart += matmul(tmp, x_subsubpart, sqrt_r_inv)
            
            x_subsubpart.fill(0)
            for t in xrange(self.q[n]):                         #Extra term to take care of h_ext..
                x_subsubpart += self.h_ext(n, s, t) * self.A[n][t] #it may be more effecient to squeeze this into the nn term...
            x_subpart += matmul(tmp, x_subsubpart, sqrt_r)
            
            x_part += matmul(None, x_subpart, Vsh[s])
                
        x += matmul(None, sqrt_l, x_part)
            
        if n > 1:
            x_part.fill(0)
            for s in xrange(self.q[n]):     #~2nd line
                x_subsubpart.fill(0)
                for t in xrange(self.q[n + 1]):
                    x_subsubpart += matmul(tmp, H(self.A[n - 1][t]), self.l[n - 2], self.C[n - 1][t, s])
                x_part += matmul(None, x_subsubpart, sqrt_r, Vsh[s])
            x += matmul(None, sqrt_l_inv, x_part)
                
        return x
        
    def GetB(self, n):
        """Generates the B[n] tangent vector corresponding to physical evolution of the state.
        
        In other words, this returns B[n][x*] (equiv. eqn. (47) of 
        arXiv:1103.0936v2 [cond-mat.str-el]) 
        with x* the parameter matrices satisfying the Euler-Lagrange equations
        as closely as possible.
        """
        if self.q[n] * self.D[n] - self.D[n - 1] > 0:
            l_sqrt, r_sqrt, l_sqrt_inv, r_sqrt_inv = self.Get_l_r_roots(n)
            
            Vsh = self.BuildVsh(n, r_sqrt)
            
            x = self.CalcOpt_x(n, Vsh, l_sqrt, r_sqrt, l_sqrt_inv, r_sqrt_inv)
    
            B = empty_like(self.A[n])
            for s in xrange(self.q[n]):
                B[s] = matmul(B[s], l_sqrt_inv, x, H(Vsh[s]), r_sqrt_inv)
            return B
        else:
            return None
        
    def Get_l_r_roots(self, n):
        """Returns the matrix square roots (and inverses) needed to calculate B.
        
        Hermiticity of l[n] and r[n] is used to speed this up.
        If an exception occurs here, it is probably because these matrices
        are not longer Hermitian (enough).
        """
        l_sqrt = sqrtmh(self.l[n - 1])
        #l_sqrt = la.sqrtm(self.l[n - 1])
        r_sqrt =  sqrtmh(self.r[n])
        #r_sqrt =  la.sqrtm(self.r[n])        
        l_sqrt_inv = la.inv(l_sqrt) #matmul invpo() not yet working properly....
        r_sqrt_inv = la.inv(r_sqrt)  
        return l_sqrt, r_sqrt, l_sqrt_inv, r_sqrt_inv
    
    def TakeStep(self, dtau): #simple, forward Euler integration     
        """Performs a complete forward-Euler step of imaginary time dtau.
        
        If dtau is itself imaginary, real-time evolution results.
        
        Here, the A's are updated as the sites are visited. Since we want all
        tangent vectors to be generated using the old state, we must delay
        updating each A[n] until we are *two* steps away (due to the direct
        dependency on A[n - 1] in CalcOpt_x).
        
        The dependencies on l, r, C and K are not a problem because we store
        all these matrices separately and do not update them at all during TakeStep().
        
        Parameters
        ----------
        dtau : complex
            The (imaginary or real) amount of imaginary time (tau) to step.
        """
        B_prev = None
        for n in xrange(1, self.N + 2):
            #V is not always defined (e.g. at the right boundary vector, and possibly before)
            if n <= self.N:
                B = self.GetB(n)
            
            if n > 1 and not B_prev is None:
                self.A[n - 1] += -dtau * B_prev
                
            B_prev = B

    def TakeStep_BEuler(self, dtau, midpoint=True):
        """A backward (implicit) integration step.
        
        Based on p. 8-10 of arXiv:1103.0936v2 [cond-mat.str-el].
        
        NOTE: Not currently working as well as expected. Iterative solution of 
              implicit equation stops converging at some point...
        
        This is made trickier by the gauge freedom. We solve the implicit equation iteratively, aligning the tangent
        vectors along the gauge orbits for each step to obtain the physically relevant difference dA. The gauge-alignment is done
        by solving a matrix equation.
        
        The iteration seems to be difficult. At the moment, iteration over the whole chain is combined with iteration over a single
        site (a single A[n]). Iteration over the chain is needed because the evolution of a single site depends on the state of the
        whole chain. The algorithm runs through the chain from N to 1, iterating a few times over each site (depending on how many
        chain iterations we have done, since iterating over a single site becomes more fruitful as the rest of the chain stabilizes).
        
        In running_update mode, which is likely the most sensible choice, the current midpoint guess is updated after visiting each
        site. I.e. The trial backwards step at site n is based on the updates that were made to site n + 1, n + 2 during the current chain
        iteration.
        
        Parameters
        ----------
        dtau : complex
            The (imaginary or real) amount of imaginary time (tau) to step.
        midpoint : bool
            Whether to use approximately time-symmetric midpoint integration,
            or just a backward-Euler step.
        """        
        #---------------------------
        #Hard-coded params:
        debug = False
        dbg_bstep = False
        safe_mode = False
        
        tol = finfo(complex128).eps * 3
        max_iter = 10
        itr_switch_mode = 10
        #---------------------------
        
        if midpoint:
            dtau = dtau / 2
        
        self.Restore_ON_R()

        #Take a copy of the current state
        A0 = empty_like(self.A)
        for n in xrange(1, self.N + 1):
            A0[n] = self.A[n].copy()
        
        #Take initial forward-Euler step
        self.TakeStep(dtau)     
        
        itr = 0
        delta = 1
        delta_prev = 0                
        final_check = False

        while delta > tol * (self.N - 1) and itr < max_iter or final_check:
            running_update = itr < itr_switch_mode
            
            A_np1 = A0[self.N]            
            
            #Prepare for next calculation of B from the new A
            self.Restore_ON_R() #updates l and r
            
            if running_update:
                self.BuildC() #we really do need all of these, since B directly uses C[n-1]
                self.CalcK()            
            
            g0_n = eye(self.D[self.N - 1], dtype=self.typ)       #g0_n is the gauge transform matrix needed to solve the implicit equation
            
            #Loop through the chain, optimizing the individual A's
            delta = 0
            for n in reversed(xrange(1, self.N)): #We start at N - 1, since the right vector can't be altered here.
                
                if not running_update: #save new A[n + 1] and replace with old version for building B
                    A_np1_new = self.A[n + 1].copy()
                    self.A[n + 1] = A_np1  
                    A_np1 = self.A[n].copy()
                    max_itr_n = 1 #wait until the next run-through of the chain to change A[n] again
                else:
                    max_itr_n = itr + 1 #do more iterations here as the outer loop progresses
                
                delta_n = 1
                itr_n = 0
                while True:
                    #Find transformation to gauge-align A0 with the backwards-obtained A.. is this enough?
                    M = matmul(None, A0[n][0], g0_n, self.r[n], H(self.A[n][0]))
                    for s in xrange(1, self.q[n]):
                        M += matmul(None, A0[n][s], g0_n, self.r[n], H(self.A[n][s]))
                    
                    g0_nm1 = la.solve(self.r[n - 1], M, sym_pos=True, overwrite_b=True)
                    
                    if not (delta_n > tol and itr_n < max_itr_n):
                        break
                    
                    B = self.GetB(n)
                    
                    if B is None:
                        delta_n = 0
                        fnorm = 0
                        break
                    
                    g0_nm1_inv = la.inv(g0_nm1) #sadly, we need the inverse too...    
                    r_dA = zeros_like(self.r[n - 1])
                    dA = empty_like(self.A[n])
                    sqsum = 0
                    for s in xrange(self.q[n]):
                        matmul(dA[s], g0_nm1_inv, A0[n][s], g0_n)
                        dA[s] -= self.A[n][s] 
                        dA[s] -= dtau * B[s]
                        if not final_check:
                            self.A[n][s] += dA[s]
    
                    for s in xrange(self.q[n]):
                        r_dA += matmul(None, dA[s], self.r[n], H(dA[s]))
                        sqsum += sum(dA[s]**2)
                    
                    fnorm = sqrt(sqsum)
                    
                    delta_n = sqrt(trace(matmul(None, self.l[n - 1], r_dA)))
                    
                    if running_update: #Since we want to use the current A[n] and A[n + 1], we need this:
                        if safe_mode:
                            self.Restore_ON_R()
                            self.BuildC()
                            self.CalcK()
                        else:
                            self.Restore_ON_R(start=n) #will also renormalize
                            self.BuildC(n_low=n-1, n_high=n)
                            self.CalcK(n_low=n, n_high=n+1)
                                        
                    itr_n += 1
                    
                    if final_check:
                        break
                    
                if not running_update: #save new A[n + 1] and replace with old version for building B
                    self.A[n + 1] = A_np1_new
                
                if debug:
                    print "delta_%d: %g, (%d iterations)" % (n, delta_n.real, itr_n) + ", fnorm = " + str(fnorm)
                delta += delta_n
                
                if safe_mode:
                    self.Upd_r()
                else:
                    self.Upd_r(n - 2, n - 1) #We only need these for the next step.
                
                g0_n = g0_nm1                
                            
            itr += 1
            if debug:
                print "delta: %g  delta delta: %g (%d iterations)" % (delta.real, (delta - delta_prev).real, itr)
            delta_prev = delta
            
            if debug:
                if final_check:
                    break
                elif delta <= tol * (self.N - 1) or itr >= max_iter:
                    print "Final check to get final delta:"
                    final_check = True
        
        #Test backward step!        
        if dbg_bstep:
            Anew = empty_like(self.A)
            for n in xrange(1, self.N + 1):
                Anew[n] = self.A[n].copy()
            
#            self.Upd_l()
#            self.Simple_renorm()
#            self.Restore_ON_R()
            self.BuildC()
            self.CalcK()        
            self.TakeStep(-dtau)
            self.Restore_ON_R()
            
            delta2 = 0            
            for n in reversed(xrange(1, self.N + 1)):
                #print n
                dA = A0[n] - self.A[n]
                #Surely this dA should also preserve the gauge choice, since both A's are in ON_R...                
                #print dA/A0[n]
                r_dA = zeros_like(self.r[n - 1])
                sqsum = 0
                for s in xrange(self.q[n]):
                    r_dA += matmul(None, dA[s], self.r[n], H(dA[s]))
                    sqsum += sum(dA[s]**2)
                delta_n = sqrt(trace(matmul(None, self.l[n - 1], r_dA)))                
                delta2 += delta_n
                if debug:
                    print "A[%d] OK?: " % n + str(allclose(dA, 0)) + ", delta = " + str(delta_n) + ", fnorm = " + str(sqrt(sqsum))
                #print delta_n
            if debug:
                print "Total delta: " + str(delta2)
                
            for n in xrange(1, self.N + 1):
                self.A[n] = Anew[n]
        else:
            delta2 = 0
            
        if midpoint:
            #Take a final step from the midpoint
            #self.Restore_ON_R() #updates l and r            
            self.Upd_l()
            self.Simple_renorm()
            self.BuildC()
            self.CalcK()
            self.TakeStep(dtau)
            
        return itr, delta, delta2
        
    def TakeStep_RK4(self, dtau):
        """Take a step using the fourth-order explicit Runge-Kutta method.
        
        This requires more memory than a simple forward Euler step, and also
        more than a backward Euler step. It is, however, far more accurate
        and stable than forward Euler, and much faster than the backward
        Euler method, since there is no need to iteratively solve an implicit
        equation.
        """
        #self.Restore_ON_R()
        
        #Take a copy of the current state
        A0 = empty_like(self.A)
        for n in xrange(1, self.N):
            A0[n] = self.A[n].copy()
            
        B_fin = empty_like(self.A)

        B_prev = None
        for n in xrange(1, self.N + 2):
            if n <= self.N:
                B = self.GetB(n) #k1
                B_fin[n] = B
                
            if not B_prev is None:
                self.A[n - 1] = A0[n - 1] - dtau/2 * B_prev
                
            B_prev = B
            
        self.Upd_l()
        self.Upd_r()
        #self.Restore_ON_R()
        self.BuildC()
        self.CalcK()
        
        B_prev = None
        for n in xrange(1, self.N + 2):
            if n <= self.N:
                B = self.GetB(n) #k2                
                
            if not B_prev is None:
                self.A[n - 1] = A0[n - 1] - dtau/2 * B_prev
                B_fin[n - 1] += 2 * B_prev
                
            B_prev = B            
            
        self.Upd_l()
        self.Upd_r()
        #self.Restore_ON_R()
        self.BuildC()
        self.CalcK()
            
        B_prev = None
        for n in xrange(1, self.N + 2):
            if n <= self.N:
                B = self.GetB(n) #k3                
                
            if not B_prev is None:
                self.A[n - 1] = A0[n - 1] - dtau * B_prev
                B_fin[n - 1] += 2 * B_prev
                
            B_prev = B
             
        self.Upd_l()
        self.Upd_r()
        #self.Restore_ON_R()
        self.BuildC()
        self.CalcK()
        
        for n in xrange(1, self.N):
            B = self.GetB(n) #k4
            if not B is None:
                B_fin[n] += B
            
        for n in xrange(1, self.N):
            if not B_fin[n] is None:
                self.A[n] = A0[n] - dtau /6 * B_fin[n]
            
    def AddNoise(self, fac):
        """Adds some random noise of a given order to the state matrices A
        This can be used to determine the influence of numerical innaccuracies
        on quantities such as observables.
        """
        for n in xrange(1, self.N + 1):
            for s in xrange(self.q[n]):
                self.A[n][s].real += (rand(self.D[n - 1], self.D[n]) - 0.5) * 2 * fac
                self.A[n][s].imag += (rand(self.D[n - 1], self.D[n]) - 0.5) * 2 * fac
                
    
    def Upd_l(self, start=-1, finish=-1):
        """Updates the l matrices using the current state.
        Implements step 5 of the TDVP algorithm or, equivalently, eqn. (41).
        (arXiv:1103.0936v2 [cond-mat.str-el])
        """
        if start < 0:
            start = 1
        if finish < 0:
            finish = self.N
        for n in xrange(start, finish + 1):
            self.l[n].fill(0)
            tmp = empty_like(self.l[n])
            for s in xrange(self.q[n]):
                self.l[n] += matmul(tmp, H(self.A[n][s]), self.l[n - 1], self.A[n][s])
    
    def Upd_r(self, n_low=-1, n_high=-1):
        """Updates the r matrices using the current state.
        Implements step 5 of the TDVP algorithm or, equivalently, eqn. (41).
        (arXiv:1103.0936v2 [cond-mat.str-el])
        """
        if n_low < 0:
            n_low = 0
        if n_high < 0:
            n_high = self.N - 1
        for n in reversed(xrange(n_low, n_high + 1)):
            self.EpsR(self.r[n], n + 1, self.r[n + 1], None)
    
    def Simple_renorm(self, upd_r=True):
        """Renormalize the state by altering A[N] by a factor.
        
        We change A[N] only, which is a column vector because D[N] = 1, using a factor
        equivalent to an almost-gauge transformation where all G's are the identity, except
        G[N], which represents the factor. Almost means G[0] =/= G[N] (the norm is allowed to change).
        
        Requires that l is up to date. 
        
        Note that this generally breaks ON_R, because this changes r[N - 1] by the same factor.
        
        By default, this also updates the r matrices to reflect the change in A[N].
        
        Parameters
        ----------
        upd_r : bool
            Whether to call Upd_r() after normalization (defaults to True).
        """
        changed = False
        itr = 0
        while not allclose(self.l[self.N], 1. + 0.j, atol=self.eps*3, rtol=0) and itr < 20:
            G_N_I = sp.sqrt(self.l[self.N][0, 0])
            
            for s in xrange(self.q[self.N]):
                self.A[self.N][s] /= G_N_I

            self.Upd_l(start=self.N, finish=self.N)
            
            changed = True
            itr += 1
        
        #We need to do this because we changed A[N]
        if upd_r and changed:
            self.Upd_r()
    
    def EpsR(self, res, n, x, o):
        """Implements the right epsilon map
        
        FIXME: Ref.
        
        Parameters
        ----------
        res : ndarray
            A matrix to hold the result (with the same dimensions as r[n - 1]). May be None.
        n : int
            The site number.
        x : ndarray
            The argument matrix. For example, using r[n] (and o=None) gives a result r[n - 1]
        o : function
            The single-site operator to use. May be None.
    
        Returns
        -------
        res : ndarray
            The resulting matrix.
        """
        if res is None:
            res = zeros_like(self.r[n - 1])
        else:
            res.fill(0.)
        tmp = empty_like(res)
        if o is None:
            for s in xrange(self.q[n]):
                res += matmul(tmp, self.A[n][s], x, H(self.A[n][s]))            
        else:
            for s in xrange(self.q[n]):
                for t in xrange(self.q[n]):
                    o_st = o(n, s, t)
                    if o_st != 0.:
                        matmul(tmp, self.A[n][t], x, H(self.A[n][s]))
                        tmp *= o_st
                        res += tmp
        return res
    
    def Restore_ON_R_n(self, n, G_n_i):
        """Transforms a single A[n] to obtain right orthonormalization.
        
        Implements the condition for right-orthonormalization from sub-section
        3.1, theorem 1 of arXiv:quant-ph/0608197v2.
        
        This function must be called for each n in turn, starting at N + 1,
        passing the gauge transformation matrix from the previous step
        as an argument.
        
        Finds a G[n-1] such that ON_R is fulfilled for n.
        
        Eigenvalues = 0 are a problem here... IOW rank-deficient matrices. 
        Apparently, they can turn up during a run, but if they do we're screwed.    
        
        The fact that M should be positive definite is used to optimize this.
        
        Parameters
        ----------
        n : int
            The site number.
        G_n_i : ndarray
            The inverse gauge transform matrix for site n obtained in the previous step (for n + 1).
    
        Returns
        -------
        G_n_m1_i : ndarray
            The inverse gauge transformation matrix for the site n - 1.
        """
        GGh_n_i = matmul(None, G_n_i, H(G_n_i)) #r[n] does not belong here. The condition is for sum(AA). r[n] = 1 is a consequence. 
        
        M = self.EpsR(None, n, GGh_n_i, None)
                    
        #The following should be more efficient than eigh():
        try:
            tu = la.cholesky(M) #Assumes M is pos. def.. It should raise LinAlgError if not.
            G_nm1 = H(invtr(tu, overwrite=True), out=tu) #G is now lower-triangular
            is_tri = True
        except sp.linalg.LinAlgError:
            print "Restore_ON_R_n: Falling back to eigh()!"
            e,Gh = la.eigh(M)
            G_nm1 = H(matmul(None, Gh, diag(1/sqrt(e) + 0.j)))
            is_tri = False
            
        
        for s in xrange(self.q[n]):                
            matmul(self.A[n][s], G_nm1, self.A[n][s], G_n_i)
            #It's ok to use the same matrix as out and as an operand here
            #since there are > 2 matrices in the chain and it is not the last argument.
        
        if is_tri:
            G_nm1_i = invtr(G_nm1, overwrite=True, lower=True)
        else:
            G_nm1_i = la.inv(G_nm1)        
        
        return G_nm1_i
        
    
    def Restore_ON_R(self, start=-1, update_l=True, normalize=True):
        """Use a gauge-transformation to restore right-orthonormalization.
        
        Implements the condition for right-orthonormalization from sub-section
        3.1, theorem 1 of arXiv:quant-ph/0608197v2.
        
        This performs an 'almost' gauge transformation, where the 'almost'
        means we allow the norm to vary (if "normalize" = True).
        
        The last step (A[1]) is done diffently to the others since G[0],
        the gauge-transf. matrix, is just a number, which can be found more
        efficiently and accurately without using matrix methods.
        
        The last step (A[1]) is important because, if we have successfully made 
        r[1] = 1 in the previous steps, it fully determines the normalization 
        of the state via r[0] ( = l[N]).
        
        Optionally (normalize=False), the function will not attempt to make
        A[1] satisfy the orthonorm. condition, and will take G[0] = 1 = G[N],
        thus performing a pure gauge-transformation, but not ensuring complete
        canonical form.
        
        It is also possible to begin the process from a site n other than N,
        in case the sites > n are known to be in the desired form already.
        
        By default, Upd_l() is called after completion, since the l's are
        now out of date.
        
        Parameters
        ----------
        start : int
            The rightmost site to start from (defaults to N)
        update_l : bool
            Whether to call Upd_l() after completion (defaults to True)
        normalize : bool
            Whether to also attempt to enforce the condition for A[1], which normalizes the state.
            
        """   
        if start < 1:
            start = self.N
        
        G_n_i = eye(self.D[start], dtype=self.typ) #This is actually just the number 1
        for n in reversed(xrange(2, start + 1)):
            G_n_i = self.Restore_ON_R_n(n, G_n_i)
            self.EpsR(self.r[n - 1], n, self.r[n], None) #Update r[n - 1], which should, ideally, now equal 1
        
        #Now do A[1]...
        #Apply the remaining G[1]^-1 from the previous step.
        for s in xrange(self.q[1]):                
            self.A[1][s] = matmul(None, self.A[1][s], G_n_i)
        self.EpsR(self.r[0], 1, self.r[1], None)
        
        #Now finish off, demanding high accuracy.
        if normalize:            
            itr = 0
            #print "r[0] = " + str(self.r[0])
            while not allclose(self.r[0], 1, atol=self.eps*2, rtol=0) and itr < 10:
                G0 = 1. / sp.sqrt(self.r[0][0, 0])
                self.A[1] *= G0
                self.EpsR(self.r[0], 1, self.r[1], None)
                #print "r[0] = " + str(self.r[0])
                itr += 1
            
        if update_l:
            res = self.Upd_l()
            return res
        else:
            return True
    
    def CheckCanonical_R(self):
        """Tests for right canonical form.
        Uses the criteria listed in sub-section 3.1, theorem 1 of arXiv:quant-ph/0608197v2.
        """
        rnsOK = True
        ls_trOK = True
        ls_herm = True
        ls_pos = True
        
        for n in xrange(1, self.N + 1):
            rnsOK = rnsOK and allclose(self.r[n], eye(self.r[n].shape[0]), atol=self.eps*2, rtol=0)
            ls_herm = ls_herm and allclose(self.l[n] - H(self.l[n]), 0, atol=self.eps*2)
            ls_trOK = ls_trOK and allclose(trace(self.l[n]), 1, atol=self.eps*2, rtol=0)
            ls_pos = ls_pos and all(la.eigvalsh(self.l[n]) > 0)
        
        normOK = allclose(self.l[self.N], 1., atol=self.eps, rtol=0)
        
        return (rnsOK, ls_trOK, ls_pos, normOK)
    
    def Expect_SS(self, o, n):
        """Computes the expectation value of a single-site operator.
        
        A single-site operator is represented as a function taking three
        integer arguments (n, s, t) where n is the site number and s, t 
        range from 0 to q[n] - 1 and define the requested matrix element <s|o|t>.
        
        Assumes that the state is normalized.
        
        Parameters
        ----------
        o : function
            The operator.
        n : int
            The site number.
        """
        res = self.EpsR(None, n, self.r[n], o)
        res = matmul(None, self.l[n - 1], res)
        return res.trace()
        
    def Expect_SS_Cor(self, o1, o2, n1, n2):
        """Computes the correlation of two single site operators acting on two different sites.
        
        See Expect_SS().
        
        n1 must be smaller than n2.
        
        Assumes that the state is normalized.
        
        Parameters
        ----------
        o1 : function
            The first operator, acting on the first site.
        o2 : function
            The second operator, acting on the second site.
        n1 : int
            The site number of the first site.
        n2 : int
            The site number of the second site (must be > n1).
        """        
        r_n = self.EpsR(None, n2, self.r[n2], o2)

        for n in reversed(xrange(n1 + 1, n2)):
            r_n = self.EpsR(None, n, r_n, None)

        r_n = self.EpsR(None, n1, r_n, o1)   
         
        res = matmul(None, self.l[n1 - 1], r_n)
        return res.trace()
        
    def DensityMatrix_2S(self, n1, n2):
        """Returns a reduced density matrix for a pair of sites.
        
        Parameters
        ----------
        n1 : int
            The site number of the first site.
        n2 : int
            The site number of the second site (must be > n1).        
        """
        rho = empty((self.q[n1] * self.q[n2], self.q[n1] * self.q[n2]), dtype=complex128)
        r_n2 = empty_like(self.r[n2 - 1])
        r_n1 = empty_like(self.r[n1 - 1])
        tmp = empty_like(self.r[n1 - 1])
        
        for s2 in xrange(self.q[n2]):
            for t2 in xrange(self.q[n2]):
                matmul(r_n2, self.A[n2][t2], self.r[n2], H(self.A[n2][s2]))
                
                r_n = r_n2
                for n in reversed(xrange(n1 + 1, n2)):
                    r_n = self.EpsR(None, n, r_n, None)        
                    
                for s1 in xrange(self.q[n1]):
                    for t1 in xrange(self.q[n1]):
                        matmul(r_n1, self.A[n1][t1], r_n, H(self.A[n1][s1]))
                        matmul(tmp, self.l[n1 - 1], r_n1)
                        rho[s1 * self.q[n1] + s2, t1 * self.q[n1] + t2] = tmp.trace()
        return rho
    
    def SaveState(self, file):
        save(file, self.A)
        
    def LoadState(self, file):
        self.A = load(file)
        
class evoMPS_TDVP_Uniform:
    odr = 'C'
    typ = complex128
    
    def __init__(self, D, q):
        self.D = D
        self.q = q
        
        self.A = zeros((q, D, D), dtype=self.typ, order=self.odr)
        
        for s in xrange(self.q):
            self.A[s] = eye(D)