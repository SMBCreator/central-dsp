package io.centraldsp.bidder.controller;

import io.centraldsp.bidder.model.ortb.BidRequest;
import io.centraldsp.bidder.model.ortb.BidResponse;
import io.centraldsp.bidder.service.BidEngineService;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

/**
 * Main OpenRTB 2.5 endpoint.
 *
 * <p>SSPs send Bid Requests to POST /bidder/ortb.
 * We respond within {@code bidder.timeout} ms or the SSP discards the response.</p>
 *
 * <p>Protocol:
 * <ul>
 *   <li>204 No Content — no bid (preferred over empty 200 for some SSPs)</li>
 *   <li>200 OK + BidResponse JSON — bid placed</li>
 * </ul>
 * </p>
 */
@Slf4j
@RestController
@RequestMapping("/bidder")
@RequiredArgsConstructor
public class BidderController {

    private final BidEngineService bidEngine;

    /**
     * OpenRTB 2.5 Bid Request endpoint.
     *
     * <p>Accept: application/json (standard) or application/x-openrtb-bid+json.
     * Both content types are supported for compatibility.</p>
     *
     * @param bidRequest deserialized BidRequest from SSP
     * @return 200 + BidResponse if we place a bid, 204 if no bid
     */
    @PostMapping(
            value = "/ortb",
            consumes = {MediaType.APPLICATION_JSON_VALUE, "application/x-openrtb-bid+json"},
            produces = MediaType.APPLICATION_JSON_VALUE
    )
    public ResponseEntity<BidResponse> bid(@RequestBody BidRequest bidRequest) {
        // Validate minimal required fields
        if (bidRequest.getId() == null || bidRequest.getImp() == null || bidRequest.getImp().isEmpty()) {
            log.warn("[Bidder] Invalid BidRequest: missing id or imp");
            return ResponseEntity.badRequest().build();
        }

        // Test mode: process normally but log it
        if (bidRequest.isTest()) {
            log.debug("[Bidder] TEST request id={}", bidRequest.getId());
        }

        BidResponse response = bidEngine.process(bidRequest);

        // No seatbid = no bid. Return 204 (standard no-bid response).
        if (response.getSeatbid() == null || response.getSeatbid().isEmpty()) {
            return ResponseEntity.noContent().build();
        }

        return ResponseEntity.ok(response);
    }

    /**
     * Some SSPs probe /bidder with GET to validate the endpoint is alive.
     */
    @GetMapping("/ortb")
    public ResponseEntity<String> probe() {
        return ResponseEntity.ok("{\"status\":\"ready\"}");
    }
}
