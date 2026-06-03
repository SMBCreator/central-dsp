package io.centraldsp.bidder.model.ortb;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Data;

@Data
@JsonIgnoreProperties(ignoreUnknown = true)
public class User {
    @JsonProperty("id")     private String id;
    @JsonProperty("buyeruid") private String buyeruid;
    @JsonProperty("yob")    private Integer yob;
    @JsonProperty("gender") private String gender;
}
