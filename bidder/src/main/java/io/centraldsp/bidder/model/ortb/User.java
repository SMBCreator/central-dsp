package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * OpenRTB 2.5 §3.2.20 — User object.
 */
@Data
@NoArgsConstructor
@JsonIgnoreProperties(ignoreUnknown = true)
public class User {

    /** Exchange-specific ID for the user. */
    @JsonProperty("id")
    private String id;

    /** Buyer-specific ID for the user (set in cookie by DSP). */
    @JsonProperty("buyeruid")
    private String buyeruid;

    /** Year of birth as a 4-digit integer. */
    @JsonProperty("yob")
    private Integer yob;

    /** Gender: "M"=male, "F"=female, "O"=known other. */
    @JsonProperty("gender")
    private String gender;

    /** Comma separated list of keywords, interests or intent. */
    @JsonProperty("keywords")
    private String keywords;

    /** Location of the user's home base. */
    @JsonProperty("geo")
    private Geo geo;
}
