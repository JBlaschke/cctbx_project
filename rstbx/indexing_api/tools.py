from __future__ import absolute_import, division, print_function

import scitbx
import six
import warnings
from rstbx.dps_core.cell_assessment import unit_cell_too_small
from rstbx.indexing_api import cpp_absence_test
from six.moves import range

def _distance(a):
  return a[0]*a[0] + a[1]*a[1] + a[2]*a[2]

def _radcmp(a,b):
  #gives -1,0,1 depending on a<b, a==b, a>b
  ad = _distance(a)
  bd = _distance(b)
  if ad<bd: return -1
  if ad==bd:
    #a and b have same length, but sort with positive numbers higher in list
    # purely for aesthetic reasons--it makes ordering look cleaner
    aL = a[0] + a[1] + a[2]
    bL = b[0] + b[1] + b[2]
    if aL > bL: return -1
    if aL < bL: return 1
    return 0
  return 1

# modularities 2,3,5 were sufficient for every two-image case
# need up to 11 for Fig 4 in the single-image indexing
_modularities = [2,3,5]
_max_spiral = max(_modularities)

def _generate_spiral_order():  #This is G0 in the paper
  if six.PY3:
    warnings.warn("rstbx.indexing_api.tools not supported on Python 3", stacklevel=3)
    return []
  points = []
  for x in range(_max_spiral,-_max_spiral-1,-1):
    for y in range(_max_spiral,-_max_spiral-1,-1):
      for z in range(_max_spiral,-_max_spiral-1,-1):
        np = (x,y,z)
        points.append(np)
  points.sort(_radcmp)
  points.remove((0,0,0))
  return points

_spiral_order = _generate_spiral_order()

def _good_pred(obs):
    good = 0
    for i in range(obs.size()):
      o = obs[i]
      if abs(o[0] - round(o[0]))<0.2 and abs(o[1] - round(o[1]))<0.2 and \
         abs(o[2] - round(o[2]))<0.2:
         good+=1
    bad = obs.size()-good
    return good,bad

def _is_collinear(x,y): # X x Y cross product is zero
  return x[0]*y[1]-x[1]*y[0]==0 and x[1]*y[2]-x[2]*y[1]==0 and x[2]*y[0]-x[0]*y[2]==0

def _is_coplanar(x,y,z):
  #triple product is zero; (X x Y).Z
  x = scitbx.matrix.row(x)
  y = scitbx.matrix.row(y)
  z = scitbx.matrix.row(z)
  return x.cross(y).dot(z)==0

def _divide(np):
  if np[0]%2 == 0 and np[1]%2== 0 and np[2]%2==0:
    return (np[0]/2,np[1]/2,np[2]/2)
  else:
    return np

def _generate_vector_representations():  #This is G1 in the paper
    '''The vector representations connote systematic absence conditions.
    For example, the vector v = (1,2,3) means H + 2K + 3L = ?n,
    where the ? represents the modularity (2,3,5,...) specified elsewhere
    '''
    conditions_lhs = []
    for np in _spiral_order:
      if _distance(np) > 6: continue
      collinear_match = 0
      for item in conditions_lhs:
        if _is_collinear(np,item): collinear_match = 1
      if not collinear_match:
        conditions_lhs.append(_divide(np))
    return conditions_lhs

def _generate_reindex_transformations():
    '''The reindex transformations are specific for a particular
    presence condition, such as H + 2K + 3L = 5n.  The transformation
    is applied in reciprocal space, and is intended to change the
    original incorrect basis set a*',b*',c*' into the correct basis
    set a*,b*,c*. The meaning of the correction matrix A is as follows:

           a* = A00(a*') + A01(b*') + A02(c*')
           b* = A10(a*') + A11(b*') + A12(c*')
           c* = A20(a*') + A21(b*') + A22(c*')

    The choice of A is not unique, we use an algorithm to select a
    particular solution.  Briefly, for the first row of A choose the row
    vector HKL which satisfies the presence condition, and is shortest in
    length.  For the second row choose the next shortest allowed row vector
    that is not collinear with the first.  The third allowed row vector is
    the next shortest not coplanar with the first two.  We check to see
    that the determinant is positive (or switch first two rows) and of
    magnitude equal to the mod factor; this assures that the unit cell will
    be reduced in volume by the appropriate factor.

    Our approach sometimes backfires:  an already too-small unit cell can
    produce a positive absence test; the cell will then be reduced in volume
    indefinitely.  Therefore the application always uses a cell volume filter
    after making the correction.
    '''
    vecrep = _generate_vector_representations()
    reindex = []
    for vec in vecrep:
      for mod in _modularities:
        #first point
        for pt in _spiral_order:
          if (vec[0]*pt[0] + vec[1]*pt[1] + vec[2]*pt[2])%mod == 0:
            first = pt
            break
        #second point
        for pt in _spiral_order:
          if (vec[0]*pt[0] + vec[1]*pt[1] + vec[2]*pt[2])%mod == 0 and \
            not _is_collinear(first,pt):
            second = pt
            break
        #third point
        for pt in _spiral_order:
          if (vec[0]*pt[0] + vec[1]*pt[1] + vec[2]*pt[2])%mod == 0 and \
            not _is_coplanar(first,second,pt):
            third = pt
            break
        A = scitbx.matrix.sqr(first+second+third)
        if A.determinant()<0: A = scitbx.matrix.sqr(second + first + third)
        assert A.determinant()==mod
        reindex.append({'mod':mod,'vec':vec,'trans':A,})
        #print "found pts",A.elems,"for vec",vec,"mod",mod
    return reindex

R = _generate_reindex_transformations()

class AbsenceHandler:
  def __init__(self):
    self.recursion_limit=8

  def absence_detected(self,hkllist):
    self.hkl = hkllist
    self.N   = self.hkl.size()
    self.flag = None

    for test in R:
        cum = cpp_absence_test(self.hkl,test['mod'],test['vec'])
        for counter in range(test['mod']):
          #print test['vec'],test['mod'],float(cum[counter])/self.N
          if float(cum[counter])/self.N > 0.8 and counter==0:
            # (if counter != 0 there is no obvious way to correct this)
            #print "Detected exclusive presence of %dH %dK %dL = %dn, remainder %d"%(
            #         test['vec'][0],test['vec'][1],test['vec'][2],test['mod'],counter)
            self.flag = {'vec':test['vec'],'mod':test['mod'],
                         'remainder':counter, 'trans':test['trans'].elems}
            return 1
    return 0

  def correct(self,orientation):
    if self.flag is None:
      raise RuntimeError("no correction necessary")
    M1 = scitbx.matrix.sqr(self.flag['trans'])
    corrected = orientation.change_basis(M1.transpose().elems)
    unit_cell_too_small(corrected.unit_cell(),cutoff = 100.)
    return corrected

  def list(self,hkllist):
      self.hkl = hkllist
      count = 0
      for m in self.hkl:
        print(count,m)
        print("                ",(m[0]%2,m[1]%2,m[2]%2), end=' ')
        print((m[0]%3,m[1]%3,m[2]%3), end=' ')
        print(((m[1]-m[2])%2,(m[1]+m[2])%2), end=' ')
        print(((m[2]-m[0])%2,(m[2]+m[0])%2), end=' ')
        print(((m[0]-m[1])%2,(m[0]+m[1])%2))
        count+=1

if __name__=='__main__':
  def pelem(arg):
    return arg.elems.__repr__()
  scitbx.matrix.sqr.__repr__ = pelem
  import pprint
  pprint.pprint( R)
  print(len(R))
