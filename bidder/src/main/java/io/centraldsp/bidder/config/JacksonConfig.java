package io.centraldsp.bidder.config;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;

/**
 * Jackson ObjectMapper tuned for OpenRTB.
 *
 * <p>Key settings:
 * <ul>
 *   <li>FAIL_ON_UNKNOWN_PROPERTIES = false — SSPs add non-standard "ext" fields.
 *       We must not reject requests because of extension fields.</li>
 *   <li>WRITE_DATES_AS_TIMESTAMPS = false — ISO 8601 for log readability.</li>
 *   <li>FAIL_ON_NULL_FOR_PRIMITIVES = false — some SSPs send null for integers.</li>
 * </ul>
 * </p>
 */
@Configuration
public class JacksonConfig {

    @Bean
    @Primary
    public ObjectMapper objectMapper() {
        ObjectMapper mapper = new ObjectMapper();

        // Critical for OpenRTB: SSPs add extension fields we don't model.
        mapper.configure(DeserializationFeature.FAIL_ON_UNKNOWN_PROPERTIES, false);

        // Gracefully handle null where primitive expected.
        mapper.configure(DeserializationFeature.FAIL_ON_NULL_FOR_PRIMITIVES, false);

        // Accept single values as arrays (some SSPs send "imp" as object, not array).
        mapper.configure(DeserializationFeature.ACCEPT_SINGLE_VALUE_AS_ARRAY, true);

        // Don't fail when serializing empty beans.
        mapper.configure(SerializationFeature.FAIL_ON_EMPTY_BEANS, false);

        // Don't serialize null fields in BidResponse (smaller payload).
        // Note: @JsonInclude(NON_NULL) on BidResponse handles this at class level.
        // This is a global fallback.
        mapper.setSerializationInclusion(
                com.fasterxml.jackson.annotation.JsonInclude.Include.NON_NULL
        );

        return mapper;
    }
}
