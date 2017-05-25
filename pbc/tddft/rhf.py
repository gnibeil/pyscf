#!/usr/bin/env python
#
# Author: Qiming Sun <osirpt.sun@gmail.com>
#
# Ref:
# Chem Phys Lett, 256, 454
# J. Mol. Struct. THEOCHEM, 914, 3
#

from functools import reduce
import numpy
from pyscf import lib
from pyscf.lib import logger
from pyscf.ao2mo import _ao2mo
from pyscf.tddft import rhf


class TDA(rhf.TDA):
#FIXME: numerically unstable?
    def __init__(self, mf):
        self.cell = mf.cell
        #self.conv_tol = 1e-7
        rhf.TDA.__init__(self, mf)

    def get_vind(self, mf):
        mo_coeff = mf.mo_coeff
        mo_energy = mf.mo_energy
        mo_occ = mf.mo_occ
        nkpts = len(mo_occ)
        nao, nmo = mo_coeff.shape[1:]
        orbo = []
        orbv = []
        for k in range(nkpts):
            nocc = numpy.count_nonzero(mo_occ[k]>0)
            nvir = nmo - nocc
            orbo.append(mo_coeff[k,:,:nocc])
            orbv.append(mo_coeff[k,:,nocc:])
        eai = _get_eai(mo_energy, mo_occ)

        def vind(zs):
            nz = len(zs)
            dm1s = [_split_vo(z, mo_occ) for z in zs]
            dmvo = numpy.empty((nz,nkpts,nao,nao), dtype=numpy.complex128)
            for i in range(nz):
                dm1 = dm1s[i]
                for k in range(nkpts):
                    dmvo[i,k] = reduce(numpy.dot, (orbv[k], dm1[k], orbo[k].T.conj()))

            vj, vk = mf.get_jk(mf.cell, dmvo, hermi=0)
            if self.singlet:
                vhf = vj*2 - vk
            else:
                vhf = -vk

            v1s = []
            for i in range(nz):
                dm1 = dm1s[i]
                for k in range(nkpts):
                    v1vo = reduce(numpy.dot, (orbv[k].T.conj(), vhf[i,k], orbo[k]))
                    v1vo += eai[k] * dm1[k]
                    v1s.append(v1vo.ravel())
            return lib.asarray(v1s).reshape(nz,-1)
        return vind

    def kernel(self, x0=None):
        '''TDA diagonalization solver
        '''
        self.check_sanity()

        mo_energy = self._scf.mo_energy
        mo_occ = self._scf.mo_occ
        eai = numpy.hstack([x.ravel() for x in _get_eai(mo_energy, mo_occ)])

        if x0 is None:
            x0 = self.init_guess(eai, self.nstates)

        precond = self.get_precond(eai)
        vind = self.get_vind(self._scf)

        self.e, x1 = lib.davidson1(vind, x0, precond,
                                   tol=self.conv_tol,
                                   nroots=self.nstates, lindep=self.lindep,
                                   max_space=self.max_space,
                                   verbose=self.verbose)[1:]
# 1/sqrt(2) because self.x is for alpha excitation amplitude and 2(X^+*X) = 1
        self.xy = [(_split_vo(xi*numpy.sqrt(.5), mo_occ), 0) for xi in x1]
        return self.e, self.xy
CIS = TDA


class TDHF(rhf.TDHF):
    def __init__(self, mf):
        raise RuntimeError
        self.cell = mf.cell
        #self.conv_tol = 1e-7
        rhf.TDHF.__init__(self, mf)

    def get_vind(self, mf):
        '''
        [ A   B ][X]
        [-B* -A*][Y]
        '''
        mo_coeff = mf.mo_coeff
        mo_energy = mf.mo_energy
        mo_occ = mf.mo_occ
        nkpts = len(mo_occ)
        nao, nmo = mo_coeff.shape[1:]
        orbo = []
        orbv = []
        for k in range(nkpts):
            nocc = numpy.count_nonzero(mo_occ[k]>0)
            nvir = nmo - nocc
            orbo.append(mo_coeff[k,:,:nocc])
            orbv.append(mo_coeff[k,:,nocc:])
        eai = _get_eai(mo_energy, mo_occ)

        def vind(xys):
            nz = len(xys)
            nx = xys[0].size // 2
            dmxs = [_split_vo(xy[:nx], mo_occ) for xy in xys]
            dmys = [_split_vo(xy[nx:], mo_occ) for xy in xys]
            dmvo = numpy.empty((nz,nkpts,nao,nao), dtype=numpy.complex128)
            for i in range(nz):
                dmx = dmxs[i]
                dmy = dmys[i]
                for k in range(nkpts):
                    dmvo[i,k] = reduce(numpy.dot, (orbv[k], dmx[k], orbo[k].T.conj()))
                    dmvo[i,k]+= reduce(numpy.dot, (orbo[k], dmy[k].T, orbv[k].T.conj()))

            vj, vk = mf.get_jk(mf.cell, dmvo, hermi=0)
            if self.singlet:
                vhf = vj*2 - vk
            else:
                vhf = -vk

            v1s = []
            for i in range(nz):
                dmx = dmxs[i]
                dmy = dmys[i]
                v1xs = []
                v1ys = []
                for k in range(nkpts):
                    v1x = reduce(numpy.dot, (orbv[k].T.conj(), vhf[i,k], orbo[k]))
                    v1y = reduce(numpy.dot, (orbo[k].T.conj(), vhf[i,k], orbv[k])).T
                    v1x+= eai[k] * dmx[k]
                    v1y+= eai[k] * dmy[k]
                    v1xs.append(v1x.ravel())
                    v1ys.append(-v1y.ravel())
                v1s.extend(v1xs)
                v1s.extend(v1ys)
            return lib.asarray(v1s).reshape(nz,-1)
        return vind

    def kernel(self, x0=None):
        '''TDHF diagonalization with non-Hermitian eigenvalue solver
        '''
        self.check_sanity()

        mo_energy = self._scf.mo_energy
        mo_occ = self._scf.mo_occ
        eai = numpy.hstack([x.ravel() for x in _get_eai(mo_energy, mo_occ)])

        if x0 is None:
            x0 = self.init_guess(eai, self.nstates)

        precond = self.get_precond(eai.ravel())
        vind = self.get_vind(self._scf)

        # We only need positive eigenvalues
        def pickeig(w, v, nroots, envs):
            realidx = numpy.where((abs(w.imag) < 1e-4) & (w.real > 0))[0]
            idx = realidx[w[realidx].real.argsort()]
            return w[idx].real, v[:,idx].real, idx

        w, x1 = lib.davidson_nosym1(vind, x0, precond,
                                    tol=self.conv_tol,
                                    nroots=self.nstates, lindep=self.lindep,
                                    max_space=self.max_space, pick=pickeig,
                                    verbose=self.verbose)[1:]
        self.e = w
        def norm_xy(z):
            x, y = z.reshape(2,-1)
            norm = 2*(lib.norm(x)**2 - lib.norm(y)**2)
            norm = 1/numpy.sqrt(norm)
            x *= norm
            y *= norm
            return _split_vo(x, mo_occ), _split_vo(y, mo_occ)
        self.xy = [norm_xy(z) for z in x1]

        return self.e, self.xy
RPA = TDHF


def _get_eai(mo_energy, mo_occ):
    eai = []
    nocc = numpy.sum(mo_occ > 0, axis=1)
    for k, no in enumerate(nocc):
        ai = lib.direct_sum('a-i->ai', mo_energy[k,no:], mo_energy[k,:no])
        eai.append(ai)
    return eai

def _split_vo(vo, mo_occ):
    nmo = mo_occ.shape[-1]
    nocc = numpy.sum(mo_occ > 0, axis=1)
    z = []
    ip = 0
    for k, no in enumerate(nocc):
        nv = nmo - no
        z.append(vo[ip:ip+nv*no].reshape(nv,no))
        ip += nv * no
    return z


if __name__ == '__main__':
    from pyscf.pbc import gto
    from pyscf.pbc import scf
    cell = gto.Cell()
    cell.unit = 'B'
    cell.atom = '''
    C  0.          0.          0.        
    C  1.68506879  1.68506879  1.68506879
    '''
    cell.a = '''
    0.      1.7834  1.7834
    1.7834  0.      1.7834
    1.7834  1.7834  0.    
    '''

    cell.basis = 'gth-szv'
    cell.pseudo = 'gth-pade'
    cell.gs = [9]*3
    cell.build()
    mf = scf.KRHF(cell, cell.make_kpts([2,1,1])).set(exxdiv=None).run()

    td = TDA(mf)
    td.verbose = 5
    print(td.kernel()[0] * 27.2114)

#    from pyscf.pbc import tools
#    scell = tools.super_cell(cell, [2,1,1])
#    mf = scf.RHF(scell).run()
#    td = rhf.TDA(mf)
#    td.verbose = 5
#    print(td.kernel()[0] * 27.2114)

    td = TDHF(mf)
    td.verbose = 5
    print(td.kernel()[0] * 27.2114)