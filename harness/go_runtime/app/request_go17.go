//go:build go1.7
// +build go1.7

package main

import "net/http"

func requestForWork(parent *http.Request, address string) (*http.Request, error) {
	request, err := http.NewRequest("GET", address, nil)
	if err != nil {
		return nil, err
	}
	return request.WithContext(parent.Context()), nil
}
