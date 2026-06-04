package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

/**
 * OpenRTB 2.5 §3.2.9 — Native object.
 * The native object requests a native ad.
 */
@Data
@NoArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class Native {

    /**
     * Request payload complying with the Native Ad Specification.
     * Passed as a JSON string (serialized NativeRequest).
     */
    @JsonProperty("request")
    private String request;

    /** Version of the Native Markup version in use. */
    @JsonProperty("ver")
    private String ver;

    /** List of supported API frameworks for this impression (OpenRTB List 5.6). */
    @JsonProperty("api")
    private List<Integer> api;

    /** Blocked creative attributes (OpenRTB List 5.3). */
    @JsonProperty("battr")
    private List<Integer> battr;
}
