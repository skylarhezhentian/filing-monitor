# filing-monitor

This is a demo lightweight SEC filing comparison tool for public equity research. Enter a ticker to pull recent 10-Q, 10-K, and 8-K filings from EDGAR, compare each against the prior comparable filing, and highlight changed language around liquidity, debt, risk factors, controls, and other investor-relevant topics.

## Run the Streamlit app

```bash
python -m pip install -r requirements.txt
streamlit run app.py
```

The app uses SEC EDGAR data endpoints. To identify your app in SEC requests, optionally set a custom user agent before running:

```bash
export SEC_USER_AGENT="filing-monitor your-name your-email@example.com"
```
