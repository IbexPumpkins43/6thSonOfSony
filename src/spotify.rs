use anyhow::{anyhow, Context, Result};
use reqwest::Client;
use serde::Deserialize;

#[derive(Clone)]
pub struct SpotifyResolver {
    client: Client,
    client_id: Option<String>,
    client_secret: Option<String>,
}

#[derive(Debug, Deserialize)]
struct TokenResponse {
    access_token: String,
}

#[derive(Debug, Deserialize)]
struct SpotifyTrack {
    name: String,
    artists: Vec<SpotifyArtist>,
}

#[derive(Debug, Deserialize)]
struct SpotifyArtist {
    name: String,
}

impl SpotifyResolver {
    pub fn from_env(client: Client) -> Self {
        Self {
            client,
            client_id: std::env::var("SPOTIFY_CLIENT_ID").ok().filter(|s| !s.is_empty()),
            client_secret: std::env::var("SPOTIFY_CLIENT_SECRET").ok().filter(|s| !s.is_empty()),
        }
    }

    pub fn is_configured(&self) -> bool {
        self.client_id.is_some() && self.client_secret.is_some()
    }

    pub async fn track_to_search_query(&self, spotify_url: &str) -> Result<String> {
        let track_id = extract_track_id(spotify_url)
            .ok_or_else(|| anyhow!("Only Spotify track URLs are supported in this prototype."))?;

        let client_id = self
            .client_id
            .as_deref()
            .ok_or_else(|| anyhow!("SPOTIFY_CLIENT_ID is not configured."))?;
        let client_secret = self
            .client_secret
            .as_deref()
            .ok_or_else(|| anyhow!("SPOTIFY_CLIENT_SECRET is not configured."))?;

        let token = self
            .client
            .post("https://accounts.spotify.com/api/token")
            .basic_auth(client_id, Some(client_secret))
            .form(&[("grant_type", "client_credentials")])
            .send()
            .await
            .context("Spotify token request failed")?
            .error_for_status()
            .context("Spotify token request returned an error")?
            .json::<TokenResponse>()
            .await
            .context("Could not decode Spotify token response")?;

        let track = self
            .client
            .get(format!("https://api.spotify.com/v1/tracks/{track_id}"))
            .bearer_auth(token.access_token)
            .send()
            .await
            .context("Spotify track request failed")?
            .error_for_status()
            .context("Spotify track request returned an error")?
            .json::<SpotifyTrack>()
            .await
            .context("Could not decode Spotify track response")?;

        let artists = track
            .artists
            .into_iter()
            .map(|a| a.name)
            .collect::<Vec<_>>()
            .join(", ");

        Ok(format!("{} {}", artists, track.name))
    }
}

fn extract_track_id(input: &str) -> Option<String> {
    let url = url::Url::parse(input).ok()?;
    if url.host_str()? != "open.spotify.com" {
        return None;
    }

    let mut segments = url.path_segments()?;
    if segments.next()? != "track" {
        return None;
    }

    let id = segments.next()?;
    if id.is_empty() {
        None
    } else {
        Some(id.to_string())
    }
}
