//go:build !go1.7
// +build !go1.7

package main

import "net/http"

func requestForWork(parent *http.Request, address string) (*http.Request, error) {
	return http.NewRequest("GET", address, nil)
}
