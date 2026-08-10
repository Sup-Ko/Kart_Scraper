"""Linear algebra primitives — pure Python, auditable, no numpy.

These are the building blocks the factor model and Monte Carlo engine need:
solving linear systems (for OLS regression) and Cholesky decomposition (for
drawing correlated random returns). Written to read like the definitions.
"""

from __future__ import annotations

import math

Matrix = list[list[float]]
Vector = list[float]


def transpose(a: Matrix) -> Matrix:
    return [list(col) for col in zip(*a)]


def matmul(a: Matrix, b: Matrix) -> Matrix:
    bt = transpose(b)
    return [[sum(x * y for x, y in zip(row, col)) for col in bt] for row in a]


def matvec(a: Matrix, v: Vector) -> Vector:
    return [sum(x * y for x, y in zip(row, v)) for row in a]


def identity(n: int) -> Matrix:
    return [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]


def solve(a: Matrix, b: Vector) -> Vector:
    """Solve A x = b by Gaussian elimination with partial pivoting.

    Raises ValueError if the system is singular to working precision.
    """
    n = len(a)
    # augmented copy so the caller's matrices are untouched
    m = [list(a[i]) + [b[i]] for i in range(n)]

    for col in range(n):
        # partial pivot: use the row with the largest absolute leading value
        pivot = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-14:
            raise ValueError("matrix is singular")
        m[col], m[pivot] = m[pivot], m[col]

        pv = m[col][col]
        for r in range(col + 1, n):
            factor = m[r][col] / pv
            if factor == 0.0:
                continue
            for c in range(col, n + 1):
                m[r][c] -= factor * m[col][c]

    # back-substitution
    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        s = m[i][n] - sum(m[i][j] * x[j] for j in range(i + 1, n))
        x[i] = s / m[i][i]
    return x


def cholesky(a: Matrix, ridge: float = 1e-10) -> Matrix:
    """Cholesky factor L with A = L·Lᵀ, for a symmetric positive-definite A.

    Sample covariance matrices are often only positive *semi*-definite (e.g.
    fewer observations than assets, or perfectly collinear series). Rather than
    fail, we add a tiny multiple of the identity — a standard "ridge" fix — and
    retry, escalating until the factorization succeeds.
    """
    n = len(a)
    scale = max((abs(a[i][i]) for i in range(n)), default=1.0) or 1.0

    while ridge < scale * 1e3:
        L = [[0.0] * n for _ in range(n)]
        ok = True
        for i in range(n):
            for j in range(i + 1):
                s = sum(L[i][k] * L[j][k] for k in range(j))
                if i == j:
                    d = a[i][i] + ridge - s
                    if d <= 0:
                        ok = False
                        break
                    L[i][j] = math.sqrt(d)
                else:
                    L[i][j] = (a[i][j] - s) / L[j][j]
            if not ok:
                break
        if ok:
            return L
        ridge *= 10
    raise ValueError("matrix is not positive definite even with ridge correction")


class OLSResult:
    """Result of an ordinary least squares fit, with the diagnostics we need."""

    def __init__(self, alpha: float, betas: Vector, r_squared: float,
                 residuals: Vector, resid_var: float):
        self.alpha = alpha
        self.betas = betas
        self.r_squared = r_squared
        self.residuals = residuals
        self.resid_var = resid_var


def ols(y: Vector, x_columns: list[Vector], intercept: bool = True) -> OLSResult:
    """Least-squares regression of y on the given predictor columns.

    Solves the normal equations (XᵀX)β = Xᵀy. Returns the intercept, the slope
    coefficients (factor betas), R², residuals, and residual variance — the
    idiosyncratic risk that the factors do not explain.
    """
    n = len(y)
    k = len(x_columns)
    if n == 0 or k == 0:
        return OLSResult(0.0, [0.0] * k, 0.0, [0.0] * n, 0.0)

    # design matrix: leading column of 1s for the intercept
    X: Matrix = []
    for i in range(n):
        row = [1.0] if intercept else []
        row.extend(x_columns[j][i] for j in range(k))
        X.append(row)

    Xt = transpose(X)
    XtX = matmul(Xt, X)
    Xty = matvec(Xt, y)

    try:
        coef = solve(XtX, Xty)
    except ValueError:
        # collinear predictors: ridge-regularize just enough to make it solvable
        for i in range(len(XtX)):
            XtX[i][i] += 1e-8
        coef = solve(XtX, Xty)

    alpha = coef[0] if intercept else 0.0
    betas = coef[1:] if intercept else coef

    fitted = matvec(X, coef)
    residuals = [y[i] - fitted[i] for i in range(n)]

    ybar = sum(y) / n
    ss_tot = sum((v - ybar) ** 2 for v in y)
    ss_res = sum(r * r for r in residuals)
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    dof = max(1, n - k - (1 if intercept else 0))
    resid_var = ss_res / dof

    return OLSResult(alpha, list(betas), r_squared, residuals, resid_var)
