package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/redis/go-redis/v9"
)

var ctx = context.Background()

func main() {
	rdb := redis.NewClient(&redis.Options{
		Addr:     os.Getenv("REDIS_URL"),
		Password: "",
		DB:       0,
	})

	router := gin.Default()

	router.GET("/health", func(c *gin.Context) {
		c.JSON(http.StatusOK, gin.H{"status": "healthy", "service": "scanner"})
	})

	router.GET("/scan", func(c *gin.Context) {
		// Placeholder for Go concurrent scanner logic
		// In production, this would scan 7000+ securities
		log.Println("Starting full market scan...")
		
		// Simulate scan
		time.Sleep(1 * time.Second)
		
		// Publish results to Redis stream
		rdb.XAdd(ctx, &redis.XAddArgs{
			Stream: "scan_results",
			Values: map[string]interface{}{
				"timestamp": time.Now().Unix(),
				"status":    "completed",
			},
		}).Err()

		c.JSON(http.StatusOK, gin.H{"message": "Scan initiated.", "results_stream": "scan_results"})
	})

	router.Run(":8001")
}
