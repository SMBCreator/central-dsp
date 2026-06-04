package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * OpenRTB 2.5 §3.2.15 — Publisher object.
 */
@Data
@NoArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class Publisher {

    /** Exchange-specific publisher ID. */
    @JsonProperty("id")
    private String id;

    /** Publisher name. */
    @JsonProperty("name")
    private String name;

    /** Highest level domain of the publisher (e.g., "publisher.com"). */
    @JsonProperty("domain")
    private String domain;
}
