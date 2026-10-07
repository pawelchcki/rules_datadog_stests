extern double runtimeMatrixCallback(int, double);

double runtime_matrix_bridge(int number, double floating) {
    return runtimeMatrixCallback(number, floating);
}
