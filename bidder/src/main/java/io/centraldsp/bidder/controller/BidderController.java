package io.centraldsp.bidder.controller;

import io.centraldsp.bidder.model.ortb.BidRequest;
import io.centraldsp.bidder.model.ortb.BidResponse;
import io.centraldsp.bidder.service.BidEngineService;
import io.centraldsp.bidder.model.campaign.PartnerDTO;
import io.centraldsp.bidder.service.PartnerLoaderService;
import io.centraldsp.bidder.service.QpsService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.concurrent.atomic.AtomicLong;

/**
 * OpenRTB 2.5 bidding endpoint.
 * SSPs send Bid Requests here and receive Bid Responses.
 *
 * POST /bidder/ortb — main bid endpoint
 * GET  /health      — health check
 */
@Slf4j
@RestController
@RequiredArgsConstructor
public class BidderController {

    private final BidEngineService bidEngine;
    private final PartnerLoaderService partnerLoader;
    private final QpsService qpsService;

    private final AtomicLong requestCount = new AtomicLong(0);

    /**
     * Main OpenRTB 2.5 bid endpoint.
     * Accept: application/json
     * Return: 200 with BidResponse OR 204 (no bid)
     */
    @PostMapping(
        value = "/bidder/ortb",
        consumes = MediaType.APPLICATION_JSON_VALUE,
        produces = MediaType.APPLICATION_JSON_VALUE
    )
    public ResponseEntity<BidResponse> bid(@RequestBody BidRequest bidRequest) {
        long count = requestCount.incrementAndGet();

        // Log every 1000th request to avoid log spam
        if (count % 1000 == 0) {
            log.info("[Bidder] Processed {} bid requests total", count);
        }

        BidResponse response = bidEngine.process(bidRequest);

        // No seatbid = no bid → HTTP 204
        if (response.getSeatbid() == null || response.getSeatbid().isEmpty()) {
            return ResponseEntity.noContent().build();
        }

        return ResponseEntity.ok(response);
    }

    /**
     * Production bid endpoint, path-compatible with the Python bidder:
     * POST /rtb/bid/{endpointKey} with optional x-centraldsp-token header.
     * Enforces partner auth (constant-time) and the shared QPS limit.
     */
    @PostMapping(
        value = "/rtb/bid/{endpointKey}",
        consumes = MediaType.APPLICATION_JSON_VALUE,
        produces = MediaType.APPLICATION_JSON_VALUE
    )
    public ResponseEntity<BidResponse> bidForPartner(
            @PathVariable String endpointKey,
            @RequestHeader(value = "x-centraldsp-token", required = false) String sspToken,
            @RequestBody BidRequest bidRequest) {

        PartnerDTO partner = partnerLoader.byEndpointKey(endpointKey);
        if (partner == null) {
            return ResponseEntity.status(404).build();
        }
        String expected = partner.getAuthToken();
        if (expected != null && !expected.isEmpty()
                && !java.security.MessageDigest.isEqual(
                        expected.getBytes(java.nio.charset.StandardCharsets.UTF_8),
                        (sspToken == null ? "" : sspToken)
                                .getBytes(java.nio.charset.StandardCharsets.UTF_8))) {
            return ResponseEntity.status(403).build();
        }
        if (!qpsService.allow(partner.getId(), partner.getQpsLimit())) {
            return ResponseEntity.status(429).build();
        }

        requestCount.incrementAndGet();
        BidResponse response = bidEngine.process(bidRequest);
        if (response.getSeatbid() == null || response.getSeatbid().isEmpty()) {
            return ResponseEntity.noContent().build();
        }
        return ResponseEntity.ok(response);
    }

    /** Health check endpoint for Docker HEALTHCHECK and monitoring. */
    @GetMapping("/health")
    public ResponseEntity<Object> health() {
        return ResponseEntity.ok(java.util.Map.of(
                "status", "UP",
                "service", "CentralBidder",
                "requests", requestCount.get()
        ));
    }
}
