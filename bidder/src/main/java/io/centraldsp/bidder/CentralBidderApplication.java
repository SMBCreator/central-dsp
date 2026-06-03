package io.centraldsp.bidder;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
public class CentralBidderApplication {
    public static void main(String[] args) {
        SpringApplication.run(CentralBidderApplication.class, args);
    }
}
