package main

/*
extern double runtime_matrix_bridge(int, double);
*/
import "C"

//export runtimeMatrixCallback
func runtimeMatrixCallback(number C.int, floating C.double) C.double {
	return C.double(float64(number) + float64(floating))
}

func cgoRoundTrip(number int, floating float64) float64 {
	return float64(C.runtime_matrix_bridge(C.int(number), C.double(floating)))
}
